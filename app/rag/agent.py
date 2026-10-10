"""
模块 12：RAGAgent —— 组装检索组件 + LangGraph 图 + checkpointer，对外提供 SSE 流式接口。

生命周期（FastAPI lifespan 调用）：
  init()  → 连接 sqlite → AsyncSqliteSaver(setup 自动建表) → 组装检索管线 → 编译图
  close() → 关闭 sqlite 连接

generate_sse(thread_id, message)：
  以 stream_mode=["messages","updates"] 驱动图执行，把两类事件翻译成 SSE 事件流：
  - messages → token 级输出（打字机效果）
  - updates  → 节点完成状态（意图/回答状态/引用溯源）
  协议与 ai_demo email_agent 对齐：{event, data: JSON 字符串}，data 内 type 字段区分类型。

多轮会话：thread_id 即会话主键，checkpointer 按它存取消息状态；
API 层的 sessions 业务表负责列表/标题展示，双存储各司其职。
"""

import json
import logging
import os
from typing import AsyncGenerator

import aiosqlite
from langchain_core.messages import AIMessage, AIMessageChunk, AnyMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app import config
from app.common.logger import logger

from app.rag.bm25_index import get_bm25_index
from app.rag.graph import build_graph
from app.rag.hybrid_retriever import HybridRetriever
from app.rag.reranker import get_reranker
from app.rag.vectorstore import get_vectorstore

# SSE 事件类型
EVT_MESSAGE = "message"        # token 级流式输出
EVT_STATUS = "status"          # 节点/状态变化（intent、ok、no_info）
EVT_REFERENCES = "references"  # 引用溯源表
EVT_DONE = "done"              # 流结束（含完整回答）
EVT_ERROR = "error"            # 异常

def _sse(event: str, **payload) -> dict:
    """SSE 帧：{event, data: JSON 字符串}，与 email_agent 协议一致。"""
    return {"event": event, "data": json.dumps(payload, ensure_ascii=False)}


class RAGAgent:
    """RAG 问答 Agent：图执行 + 会话持久化 + SSE 流式。"""

    def __init__(self):
        self.conn: aiosqlite.Connection | None = None
        self.checkpointer: AsyncSqliteSaver | None = None
        self.graph = None

    # ==================== 生命周期 ====================

    async def init(self) -> None:
        """启动装配：checkpointer → 检索管线 → 图。"""
        await self.init_checkpointer()
        await self.init_graph()
        logger.info("RAGAgent 初始化完成")

    async def init_checkpointer(self) -> None:
        """sqlite 连接 + AsyncSqliteSaver（多轮会话状态持久化）。"""
        os.makedirs(config.DB_DIR, exist_ok=True)
        self.conn = await aiosqlite.connect(str(config.CHAT_DB_PATH))
        self.checkpointer = AsyncSqliteSaver(conn=self.conn)
        await self.checkpointer.setup()  # 自动建表
        logger.info(f"checkpointer 就绪：{config.CHAT_DB_PATH}")

    async def init_graph(self) -> None:
        """组装检索管线（进程级单例）并编译 LangGraph 图。"""
        retriever = HybridRetriever(
            vectorstore=get_vectorstore(),
            bm25=get_bm25_index(),
            reranker=get_reranker(),
        )
        self.graph = build_graph(retriever, checkpointer=self.checkpointer)
        logger.info("问答图编译完成")

    async def close(self) -> None:
        """关闭 sqlite 连接（FastAPI shutdown 时调用）。"""
        if self.conn:
            await self.conn.close()
            logger.info("sqlite 连接已关闭")

    # ==================== SSE 流式问答 ====================

    async def generate_sse(
        self, thread_id: str, message: str, user_id: str | None = None
    ) -> AsyncGenerator[dict, None]:
        """驱动图执行，产出 SSE 事件流（message/status/references/done/error）。

        注意：inputs 只传消息，thread_id 走 config —— checkpointer 自动续接该会话历史。
        """
        config_runtime = {"configurable": {"thread_id": thread_id}}
        inputs = {
            "messages": [HumanMessage(content=message)],
            "user_id": user_id,  # 身份接力棒：API 层 → state → SQL WHERE
        }
        logger.info(f"调用问答图，thread={thread_id}")

        final_answer = ""
        final_status = ""
        final_references: list[dict] = []

        try:
            # stream_mode=["messages","updates"]：token 级 + 节点级双流
            # langgraph 1.x yield 的是 {"type","ns","data"} dict（同 email_agent 写法）
            async for chunk in self.graph.astream(
                inputs,
                config=config_runtime,
                stream_mode=["messages", "updates"],
                version="v2",
            ):
                event_type = chunk["type"]
                data = chunk["data"]
                if event_type == "messages":
                    token, meta = data
                    # 只透出"生成节点"的流式增量块（两个过滤缺一不可）：
                    # 1) messages 流会透出图中所有 LLM 调用（意图路由/查询改写等），
                    #    按节点白名单过滤，避免改写问句、意图 JSON 混入回答；
                    # 2) 流式结束后 langgraph 会再发一条完整 AIMessage（非 Chunk），
                    #    跳过 —— 否则前端逐字追加后又叠加完整回答（回答重复两遍）
                    if meta.get("langgraph_node") not in ("generate", "chitchat_node", "generate_tool"):
                        continue
                    if isinstance(token, AIMessageChunk) and isinstance(token.content, str):
                        content = token.content
                        if content:
                            final_answer += content
                            yield _sse(EVT_MESSAGE, type="message", content=content)

                elif event_type == "updates":
                    for node_name, update in data.items():
                        frames, status, refs = self._node_update_frames(
                            node_name, update
                        )
                        for f in frames:
                            yield f
                        if status:
                            final_status = status
                        if refs:
                            final_references[:] = refs

            # 流结束：把最终回答 + 引用一次性发给前端收尾
            yield _sse(
                EVT_DONE,
                type="done",
                content=final_answer,
                status=final_status,
                references=final_references,
            )
        except Exception as e:  # noqa: BLE001 —— SSE 流必须由 error 帧结束
            logger.error(f"SSE 流异常：{e}", exc_info=True)
            yield _sse(EVT_ERROR, type="error", error=str(e))

    @staticmethod  # 静态方法：不依赖实例属性
    def _node_update_frames(
        node_name: str, update: dict
    ) -> tuple[list[dict], str | None, list[dict] | None]:
        """节点级更新 → SSE 帧。返回 (frames, status, references)，供生成器统一 yield。"""
        # 意图路由结果
        if node_name == "route_intent" and update.get("intent"):
            return (
                [_sse(EVT_STATUS, type="status", status=update["intent"])],
                None,
                None,
            )

        # 生成类节点（回答完成：带状态；其中 generate 带引用）
        if node_name in ("generate", "generate_no_info", "chitchat_node", "generate_tool"):
            status = update.get("status", "ok")
            frames = [_sse(EVT_STATUS, type="status", status=status)]
            refs = update.get("references") or []
            if refs:
                frames.append(
                    _sse(EVT_REFERENCES, type="references", references=refs)
                )
            return frames, status, refs or None

        return [], None, None

    # ==================== 会话消息（checkpointer 读档） ====================

    async def get_messages(self, thread_id: str) -> dict:
        """按 thread_id 读取历史消息（纯查数据库，不触发执行）。"""
        config_runtime = {"configurable": {"thread_id": thread_id}}
        state = await self.graph.aget_state(config_runtime)
        if state is None or not state.values:
            return {"messages": []}

        result = []
        for msg in state.values.get("messages", []):
            if not msg.content:
                continue
            if isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                result.append({"role": "assistant", "content": msg.content})
        return {"messages": result}

    async def clear_messages(self, thread_id: str) -> None:
        """删除该会话在 checkpointer 中的全部状态。"""
        try:
            await self.checkpointer.adelete_thread(thread_id)
            logger.info(f"已清除 thread={thread_id} 的 checkpoint")
        except Exception as e:  # noqa: BLE001
            logger.error(f"清除 checkpoint 失败：{e}", exc_info=True)


# ==================== 进程级单例 ====================

rag_agent = RAGAgent()

__all__ = ["rag_agent"]
