"""模块 14b：聊天 API（SSE 流式）。

POST /api/v1/chat/stream  body: {message, thread_id?}
- 无 thread_id → 新会话：生成 id + 登记业务表（标题 = 首条消息前 30 字）
- 有 thread_id → 续接历史（checkpointer 自动加载）+ 刷新活跃时间
响应：EventSourceResponse，事件协议与 email_agent 一致：
  session → status(intent) → message(流式 token)* → status(ok/no_info) → references? → done
"""

import json
import logging
import uuid

from fastapi import APIRouter, HTTPException,Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.models.session import session_store
from app.rag.agent import rag_agent
from app.common.security import get_current_user
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(get_current_user)])

# 会话标题：取首条消息前 30 字
TITLE_MAX_LEN = 30


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


def _frame(event: str, **payload) -> dict:
    """SSE 帧：{event, data: JSON 字符串}。"""
    return {"event": event, "data": json.dumps(payload, ensure_ascii=False)}


def _sse_response(event_stream) -> StreamingResponse:
    """把 {event, data} 帧流编码为标准 SSE 文本（手写编码，不依赖第三方 SSE 库）。

    协议：event 行 + data 行 + 空行分隔，前端按 \\n\\n 拆帧解析。
    """

    async def _stream():
        async for frame in event_stream:
            yield f"event: {frame['event']}\ndata: {frame['data']}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/stream")
async def chat_stream(req: ChatRequest, user: dict = Depends(get_current_user)) -> StreamingResponse:
    """流式问答。返回 SSE 流：先推 session 帧（前端建会话项），再推图执行事件。"""
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")

    is_new = req.thread_id is None
    thread_id = req.thread_id or f"thread_{uuid.uuid4().hex[:12]}"
    title = message[:TITLE_MAX_LEN]

    # 会话双存储的"业务表"一侧：新会话登记 / 老会话刷新时间
    if is_new:
        await session_store.create(thread_id, user["sub"], title)
    else:
        owner = await session_store.get_owner(thread_id)
        if owner is None:
            raise HTTPException(status_code=404, detail=f"会话不存在或已被删除")
        if owner != user["sub"]:
            raise HTTPException(status_code=403, detail="无权限访问他人的会话")
        await session_store.touch(thread_id)

    async def event_stream():
        # 首帧：告知前端 thread_id（前端用它创建/定位会话）
        yield _frame("session", type="session", thread_id=thread_id, title=title)
        async for frame in rag_agent.generate_sse(thread_id, message, user["sub"]):
            yield frame

    return _sse_response(event_stream())
