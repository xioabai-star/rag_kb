"""模块 11：LangGraph 问答图 —— 全系统核心。

图结构（显式 StateGraph，节点/边/分支全部代码可见、可控、可调试）：

START → route_intent ──chitchat──→ chitchat_node → END
                  ├─ kb_question → rewrite_question → retrieve ──┬→ generate → END
                  │                                               └→ generate_no_info → END
                  └─ tool_request → tool_node → generate_tool → END

设计要点：
- 意图路由：闲聊不浪费检索与 token，防幻觉
- 查询改写：多轮对话的指代消解（"那报销呢？" → 独立可检索问句）
- 空检索结果走兜底分支，绝不硬答（防幻觉第一道防线）
- references 引用溯源：回答编号 [n] 与文档块一一对应
- 流式：astream(stream_mode=["messages"]) 自动吐 token，节点内普通 ainvoke 即可
- 同步检索必须 asyncio.to_thread 包裹（不阻塞事件循环，SSE 吞吐关键）
"""

import asyncio
import logging
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel

from app import config
from app.rag.hybrid_retriever import HybridRetriever, RetrievedChunk
from app.models.reimbursement import reimbursement_store
from app.tools.amap_tools import get_amap_tools

logger = logging.getLogger(__name__)

# 生成模型（进程级单例；DEEPSEEK_API_KEY 从 .env 注入）
llm = ChatDeepSeek(model=config.GENERATE_MODEL, temperature=config.TEMPERATURE)

SYSTEM_PROMPT = """你是企业知识库智能助手，帮助员工快速查询公司制度、流程与产品信息。

回答规则（必须遵守）：
1. 只依据"参考资料"中的内容回答，禁止编造资料之外的信息；
2. 回答中使用 [1]、[2] 等编号标注信息出处（对应参考资料编号），多段引用用不同编号；
3. 参考资料不足以回答时，明确说明"知识库资料中没有相关内容"，并建议用户补充文档或换个问法；
4. 回答简洁、结构清晰，面向企业员工，使用中文；
5. 不要复述参考资料原文，用自己的话归纳要点。"""


# ==================== 意图路由（结构化输出） ====================

class RouteIntent(BaseModel):
    """意图分类结果（结构化输出，强制三选一）。"""

    intent: Literal["kb_question", "chitchat", "tool_request"]
    tool_name: Literal["reimbursement", "route", "weather"] | None = None  # 报销具名：tool_request 三选一；kb/chitchat 不填


# ==================== State ====================

class RAGState(TypedDict):
    """节点间共享的"黑板"：messages 累加，其余字段节点写入即覆盖。"""

    messages: Annotated[list[AnyMessage], add_messages]  # 完整对话历史（checkpointer 持久化）
    intent: str              # "kb_question" | "chitchat" | "tool_request"
    tool_name: str | None      # "reimbursement" | "route" | "weather"
    question: str            # 当前轮问题（可能被改写）
    docs: list[RetrievedChunk]  # 混合检索结果
    references: list[dict]   # 引用溯源表 [{index, chunk_id, filename, header, content}]
    answer: str
    status: str              # "ok" | "no_info" | "error"
    user_id: str | None      # 谁在问（API 层注入，工具按人过滤 = 数据隔离）
    tool_result: str | None  # 工具节点的产出（报销记录文本），写给 generate_tool 读


# ==================== 节点 ====================

async def route_intent(state: RAGState) -> dict:
    """意图路由：判断最新用户消息是知识库问题、闲聊还是查个人业务数据（结构化输出，三选一）。"""
    last_message = state["messages"][-1].content
    #---- 多轮：拼最近对话（与 rewrite_question 同款截断），单轮留空 = 行为与原来一致 ----
    history_text = ""
    user_count = sum(1 for m in state["messages"] if isinstance(m,HumanMessage))
    if user_count > 1:
        recent = state["messages"][-config.HISTORY_WINDOW * 2:]
        history_text = "\n".join(
            f"{'用户' if isinstance(m,HumanMessage) else '助手'}: {m.content}"
            for m in recent
            if m.content
        )

    result = await llm.with_structured_output(RouteIntent).ainvoke(
        f"""你是企业知识库的问询意图分类器。根据用户消息判断它是知识库问题、闲聊还是查个人业务数据。

         定义：
         - kb_question：询问公司制度、审批/报销流程、产品与工具的使用方法等，有明确"规定或做法"可查证的问题。例如"年假没休完能攒到明年吗"、"打卡提示设备不在范围怎么办"。
         - chitchat：问候、寒暄、闲聊、纯个人话题或与公司规定无关的问题；也包括询问助手本身能做什么、请求助手做文档之外的事（写周报、做会议纪要、翻译等）。例如"在吗"、"今天天气不错"。
         - tool_request：用户想查询他**自己的业务数据/记录/进度**，如报销单状态、审批进度等,例如"帮我查一下我的报销单"、"我上个月提交的报销审批到哪一步了"。或者用户想要查询某地的天气，例如："上海今天的天气怎么样?",或者用户想要查询某两地之间的交通路线，例如："上海到北京要怎么去?"。

         示例：
         用户：入职试用期的话，工资咋算？ → kb_question
         用户：我早上可以8点半就来上班吗？ → kb_question
         用户：报销时限是多久？ → kb_question
         用户：公司对发票丢失怎么处理？ → kb_question
         用户：出差住宿标准是多少？ → kb_question
         用户：今天中午点什么外卖好呢 → chitchat
         用户：你能做会议纪要吗？ → chitchat
         用户：帮我写一封请假邮件 → chitchat
         用户：帮我查一下我最近的报销单 → tool_request（tool_name: reimbursement）
         用户：我上个月提交的报销单到哪一步了？ → tool_request（tool_name: reimbursement）
         用户：我的差旅报销打款了没有？ → tool_request（tool_name: reimbursement）
         用户：今天天气不错 → chitchat
         用户：上海今天天气怎么样 → tool_request（tool_name: weather）
         用户：上海到北京要怎么去 → tool_request（tool_name: route）

         tool_name 填写规则：
         - 当判断为 tool_request 时，必须在三个工具名中三选一：报销类 → "reimbursement"、某地天气 → "weather"、两地交通路线 → "route"；
         - 无法判断具体工具，或意图是 chitchat/kb_question → 不填 tool_name。

         边界判断：问"公司/制度/流程怎么规定"是 kb_question；问"我的/我提交的/我上个月那笔…到哪一步/多少钱/打款没"是 tool_request——他要的是个人数据，不是制度条文。

        【多轮追问规则——仅在有多轮历史时生效】
           对话历史：
           {history_text}

           用户当前消息可能是对上一轮话题的省略式追问（如"那高铁呢"、"不能坐高铁吗"、
           "那报销呢"）——先看历史判断它在追问什么，意图跟随上一轮话题：
           - 上一轮查"株洲怎么去广州"（tool_request），当前"不能坐高铁吗"
             → tool_request（tool_name: route），这是在追问同一段路线的交通方式；
           - 上一轮查"上海天气"，当前"那北京呢" → tool_request（tool_name: weather）；
           - 上一轮问报销，当前"到哪一步了" → tool_request（tool_name: reimbursement）。
           但历史只是线索不是结论：当前问句本身完整自足时（有明确对象，如"报销时限是多久"），
           按问句本身分类，不要被历史带偏。

         如无法确定，倾向 kb_question（宁可多检索一次，不要漏掉用户真正的问题）。
         只输出判断结果，不要改写问题，不要解释理由。

         用户消息：{last_message}"""
    )
    return {"intent": result.intent, "tool_name": result.tool_name}


async def chitchat_node(state: RAGState) -> dict:
    """闲聊分支：直接聊天，不检索（省 token、防幻觉）。"""
    messages = [
        SystemMessage(
            "你是企业智能助手，当前提供三类服务："
            "1) 知识库问答：回答公司制度、流程、产品使用等文档内可查证的问题；"
            "2) 个人数据查询：查询你自己的报销单状态与进度；"
            "3) 地图查询：查询城市天气、两地之间的出行路线。"
            "用户可能在闲聊，也可能请求尚未支持的服务（写周报、做会议纪要、翻译等）——"
            "对这类请求要友好地说明当前做不到，并引导对方使用已支持的服务。"
        ),
        *state["messages"][-2:],
    ]
    resp = await llm.ainvoke(messages)
    # 完成状态 = "chitchat"（与意图一致，前端据此显示"闲聊"标签；
    # 不能用 "ok"——ok 只表示知识库问答正常完成）
    # messages 追加 AIMessage：回答必须进历史，否则多轮改写缺助手上下文
    return {
        "answer": resp.content,
        "messages": [AIMessage(content=resp.content)],
        "status": "chitchat",
        "references": [],
    }


async def rewrite_question(state: RAGState) -> dict:
    """查询改写：仅多轮（用户消息 > 1）时调用，把"最近对话 + 当前问题"
    改写为独立可检索的问句（解决指代：'那报销呢？' → '报销需要什么材料？'）。
    只有一轮时不过改写，直接返回原问题。
    多轮对话是将上下文和当前问题一起送给ai，让ai完成改写，将口语化的问题转换为独立可检索的问句。
    """

    question = state["messages"][-1].content
    user_count = sum(1 for m in state["messages"] if isinstance(m, HumanMessage))

    if user_count <= 1:
        return {"question": question}  # 单轮：原样返回，省一次调用

    recent = state["messages"][-config.HISTORY_WINDOW * 2 :]
    context_text = "\n".join(
        f"{'用户' if isinstance(m, HumanMessage) else '助手'}: {m.content}"
        for m in recent
        if m.content
    )
    rewrite_prompt = (
        "以下是多轮对话历史与当前问题。请把当前问题改写为一个不依赖上下文、"
        "可直接用于知识库检索的独立问句。只输出改写后的问句，不要其他内容。\n\n"
        f"对话历史：\n{context_text}\n\n当前问题：{question}"
    )
    rewritten = await llm.ainvoke(rewrite_prompt)
    return {"question": rewritten.content.strip() or question}


async def retrieve_node(state: RAGState, retriever: HybridRetriever) -> dict:
    """检索节点：混合检索（稠密+稀疏+RRF+精排）。
    同步库必须 to_thread —— 不阻塞事件循环（SSE 吞吐的关键）。"""
    docs = await asyncio.to_thread(retriever.retrieve, state["question"])
    return {"docs": docs}


def route_by_docs(state: RAGState) -> str:
    """条件边：检索结果为空 → 兜底分支（防幻觉第一道防线）。"""
    return "generate" if state.get("docs") else "no_info"


async def generate_node(state: RAGState) -> dict:
    """生成节点：把检索结果编号成参考资料，DeepSeek 生成带 [n] 引用的回答。"""
    docs: list[RetrievedChunk] = state["docs"]

    # 编号参考资料块（文件名 + 章节标题 + 内容）
    context_blocks = []
    references = []
    for i, chunk in enumerate(docs, start=1):
        meta = chunk.metadata
        source = f"{meta.get('filename', '')} {meta.get('header', '')}".strip()
        context_blocks.append(f"[{i}] 来源: {source}\n{chunk.content}")
        references.append(
            {
                "index": i,
                "chunk_id": chunk.chunk_id,
                "filename": meta.get("filename", ""),
                "header": meta.get("header", ""),
                "content": chunk.content,
            }
        )

    context = "\n\n".join(context_blocks)
    messages = [
        SystemMessage(SYSTEM_PROMPT),
        *state["messages"][-config.HISTORY_WINDOW * 2 :],  # 滑动窗口剪枝
        HumanMessage(f"参考资料：\n{context}\n\n问题：{state['question']}"),
    ]

    resp = await llm.ainvoke(messages)
    return {
        "answer": resp.content,
        "messages": [AIMessage(content=resp.content)],  # 回答进历史（多轮上下文）
        "references": references,
        "status": "ok",
    }

async def generate_no_info(state: RAGState) -> dict:
    """兜底分支：知识库无相关内容，固定话术，不调 LLM（快、稳、省钱）。"""
    answer = "知识库中暂无相关信息。你可以换个问法，或补充相关文档后再试。"
    return {
        "answer": answer,
        "messages": [AIMessage(content=answer)],  # 同样进历史（保持会话连贯）
        "status": "no_info",
        "references": [],
    }


# 工具参数抽取（结构化输出第二用例：意图分类与参数抽取共用 with_structured_output 机制）
class RouteParams(BaseModel):
    """从用户问句里抽取工具所需参数（LLM 即解析器）。"""

    origin: str | None = None        # 起点地名（路线用）
    destination: str | None = None   # 终点地名（路线用）
    city: str | None = None          # 城市名（天气用）


async def tool_node(state: RAGState) -> dict:
    """工具调度节点（dispatcher）：按 tool_name 分流——报销（默认）/ 出行路线 / 天气。"""
    tool_name = state.get("tool_name")
    user_msg = state["messages"][-1].content

    # ---- 多轮：拼最近对话历史（与 route_intent/rewrite_question 同款；单轮留空 = 原行为不变）----
    history_text = ""
    user_count = sum(1 for m in state["messages"] if isinstance(m, HumanMessage))
    if user_count > 1:
        recent = state["messages"][-config.HISTORY_WINDOW * 2:]
        history_text = "\n".join(
            f"{'用户' if isinstance(m, HumanMessage) else '助手'}: {m.content}"
            for m in recent
            if m.content
        )

    # ---- route：出行路线（先抽地名参数，再调高德 MCP）----
    if tool_name == "route":
        params = await llm.with_structured_output(RouteParams).ainvoke(
            f"""你是出行参数抽取器。从用户消息中抽取出发地和目的地。
                origin = 出发地，destination = 目的地。例如"从虹桥站到张江怎么走"抽成
                origin="虹桥站"，destination="张江"（只要地名本身，不要"从""到"等修饰词）。
                消息中没有的地名留空，绝不编造。
                只输出抽取结果，不要解释。

                【多轮：地名继承】
                  对话历史：
                  {history_text}
                  用户当前消息可能省略了地名（如"不能坐高铁吗"——是对上一轮路线的追问），
                  此时从对话历史里最近的路线话题继承缺失的 origin/destination；
                  "高铁""公交""打车"等是交通偏好词，不是地名，不要抽入。
                  消息与历史都没有的地名才留空。

            用户消息：{user_msg}
            """
        )
        if not params or not (params.origin and params.destination):
            return {"tool_result": "没听清出发地和目的地，请说的更具体些（例如“从虹桥站到张江怎么走”）, 再试一次。"}
        return {"tool_result": await get_amap_tools().route_plan(params.origin, params.destination)}

    elif tool_name == "weather":
        params = await llm.with_structured_output(RouteParams).ainvoke(
            f"""你是天气参数抽取器，从用户消息中抽取城市名。
            city = 城市名。例如"上海天气"抽成 city="上海"（只要城市名本身，不要"市"等修饰词）。
            出发地 origin 和 目的地 destination 留空不填
            消息中没有的城市名留空，绝不编造。
            只输出抽取结果，不要解释。

            【多轮：城市继承】
              对话历史：
              {history_text}
              用户当前消息可能省略了城市名（如"那明天呢"——接上一轮天气话题），
              此时从对话历史里最近的天气话题继承城市；消息里有城市名就以消息为准。

            用户消息：{user_msg}

        """
        )
        if not params or not params.city:
            return {"tool_result": "没听清城市名，请说的更具体些（例如“上海天气”）, 再试一次。"}
        return {"tool_result": await get_amap_tools().weather(params.city)}

    # ---- 报销（默认兜底：reimbursement 与 None 都落到这里，老会话零改动兼容）----
    rows = await reimbursement_store.list_by_user(state.get("user_id") or "")
    if not rows:
        text = "未查询到该用户的报销单记录。"
    else:
        lines = [
            f"- {row['submitted_at']} {row['expense_type']} ¥{row['amount']:.2f} [{row['status']}]"
            for row in rows
        ]
        text = "该用户的报销单记录（最近的在前）：\n" + "\n".join(lines)
    return {"tool_result": text}

TOOL_SYSTEM_PROMPT = """你是企业智能助手，负责查询员工的报销单数据。

回答规则（必须遵守）：
1. 只依据"报销单数据"中的内容回答，禁止编造记录中不存在的字段（如驳回原因、金额差异等——数据里没有就是没有，如实说明即可）；
2. 数据中查不到该用户的报销单时，如实说明，并建议检查账号是否正确或联系财务核实；
3. 回答简洁、结构清晰，面向企业员工，使用中文。
"""

TOOL_MAP_SYSTEM_PROMPT = """你是企业智能助手，负责转述地图服务（出行路线 / 天气）的查询结果。

回答规则（必须遵守）：
1. 只依据"工具返回结果"中的内容如实转述（路线方案、距离、耗时、天气等），结果里没有的信息不要补充或推测；
2. 结果若为失败提示（如"没听清出发地"），用友好口吻复述原因，并引导用户补充具体地名 / 城市名后重试；
3. 回答简洁、结构清晰，面向企业员工，使用中文。
"""

async def generate_tool(state: RAGState) -> dict:
    """工具结果生成：按 tool_name 选 prompt——报销（个人数据）与地图（外部查询）话术不同。"""
    if state.get("tool_name") in ("route", "weather"):
        sys_prompt, status = TOOL_MAP_SYSTEM_PROMPT, "tool_map"
    else:
        sys_prompt, status = TOOL_SYSTEM_PROMPT, "tool"
    messages = [
        SystemMessage(sys_prompt),
        HumanMessage(
            f"工具返回结果：\n{state['tool_result']}\n\n用户问题：{state['messages'][-1].content}"
        ),
    ]
    resp = await llm.ainvoke(messages)
    return {
        "answer": resp.content,
        "messages": [AIMessage(content=resp.content)],
        "references": [],  # 工具结果不是文档，不带 [n] 引用
        "status": status,
    }




# ==================== 图构建 ====================

def build_graph(retriever: HybridRetriever, checkpointer=None):
    """构建并编译问答图。checkpointer 传入后即具备多轮会话持久化能力。"""

    async def _retrieve(state: RAGState) -> dict:
        return await retrieve_node(state, retriever)

    builder = StateGraph(RAGState)
    builder.add_node("route_intent", route_intent)
    builder.add_node("chitchat_node", chitchat_node)
    builder.add_node("rewrite_question", rewrite_question)
    builder.add_node("retrieve", _retrieve)
    builder.add_node("generate", generate_node)
    builder.add_node("generate_no_info", generate_no_info)
    builder.add_node("tool_node", tool_node)
    builder.add_node("generate_tool", generate_tool)
    builder.add_edge(START, "route_intent")
    # 意图分流
    builder.add_conditional_edges(
        "route_intent",
        lambda s: s["intent"],
        {
            "kb_question": "rewrite_question",
            "chitchat": "chitchat_node",
            "tool_request": "tool_node",
        },
    )
    #闲聊问答分支
    builder.add_edge("chitchat_node", END)
    # 知识库问答主线
    builder.add_edge("rewrite_question", "retrieve")
    builder.add_conditional_edges(
        "retrieve", route_by_docs, {"generate": "generate", "no_info": "generate_no_info"}
    )
    builder.add_edge("generate", END)
    builder.add_edge("generate_no_info", END)

    #工具调用分支
    builder.add_edge("tool_node", "generate_tool")
    builder.add_edge("generate_tool", END)


    return builder.compile(checkpointer=checkpointer)
