"""模块 14c：会话管理 API。

GET    /api/v1/sessions                      会话列表（标题/时间）
GET    /api/v1/sessions/{thread_id}/messages 读历史消息（checkpointer 读档）
DELETE /api/v1/sessions/{thread_id}          删除会话（级联双删：业务表 + checkpointer）
"""

import logging

from fastapi import APIRouter, HTTPException, Depends

from app.models.session import session_store
from app.rag.agent import rag_agent
from app.common.security import get_current_user
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=[Depends(get_current_user)])


@router.get("")
async def list_sessions(user: dict = Depends(get_current_user)) -> dict:
    """会话列表（按最后活跃时间倒序）。"""
    return {"sessions": await session_store.list(user["sub"])}


@router.get("/{thread_id}/messages")
async def get_messages(thread_id: str, user: dict = Depends(get_current_user)) -> dict:
    """读取某会话的全部历史消息（仅 checkpointer 一侧，纯查库）。"""
    owner = await session_store.get_owner(thread_id)
    if owner is None:
        raise HTTPException(status_code=404, detail=f"会话不存在或已被删除")
    if owner != user["sub"]:
        raise HTTPException(status_code=403, detail=f"无权访问他人的会话")
    return await rag_agent.get_messages(thread_id)


@router.delete("/{thread_id}")
async def delete_session(thread_id: str, user: dict = Depends(get_current_user)) -> dict:
    """删除会话：业务表 + checkpointer 两处都删（级联）。"""
    owner = await session_store.get_owner(thread_id)
    if owner is None:
        raise HTTPException(status_code=404, detail=f"会话不存在或已被删除")
    if owner != user["sub"]:
        raise HTTPException(status_code=403, detail=f"无权访问他人的会话")
    deleted = await session_store.delete(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"会话不存在或已被删除")
    await rag_agent.clear_messages(thread_id)
    return {"ok": True}
