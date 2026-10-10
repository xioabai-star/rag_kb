"""模块 13：会话业务表（sessions）。

双存储设计：
- checkpointer（chat.db 内的 checkpoints 表）保存"消息状态"——图运行必需
- sessions 表保存"会话元数据"（标题/创建时间/活跃时间）——前端列表展示
删除会话 = 两处都删（API 层做级联），各层职责单一、互不渗透。
"""

import logging
import time
from pathlib import Path

import aiosqlite

from app import config

logger = logging.getLogger(__name__)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_id  TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '新会话',
    user_id    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


class SessionStore:
    """sessions 业务表读写（aiosqlite，与 checkpointer 共用 chat.db）。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.db_path)
        await self.conn.execute(_CREATE_SQL) # 自动建表
        await self.conn.commit()
        logger.info(f"sessions 表就绪：{self.db_path}")

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def create(self, thread_id: str, user_id: str, title: str) -> dict:
        """新会话登记（标题取首条消息摘要，由调用方传入）。"""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            "INSERT OR REPLACE INTO sessions (thread_id, user_id, title, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (thread_id, user_id, title, now, now),
        )
        await self.conn.commit()
        return {"thread_id": thread_id, "title": title, "created_at": now, "updated_at": now}

    async def touch(self, thread_id: str) -> None:
        """更新最后活跃时间（每条消息到达时调用，列表按此排序）。"""
        await self.conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE thread_id = ?",
            (time.strftime("%Y-%m-%d %H:%M:%S"), thread_id),
        )
        await self.conn.commit()

    async def get_owner(self, thread_id: str) ->dict | None:
        """查会话的房主（越权闸门用）；不存在返回 None。"""
        cur = await self.conn.execute(
            "SELECT user_id FROM sessions WHERE thread_id = ?",(thread_id,)
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def list(self,user_id: str) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT thread_id, title, created_at, updated_at"
            " FROM sessions WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )
        rows = await cur.fetchall()
        return [
            {"thread_id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3]}
            for r in rows
        ]

    async def delete(self, thread_id: str) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM sessions WHERE thread_id = ?", (thread_id,)
        )
        await self.conn.commit()
        return cur.rowcount > 0


# ==================== 进程级单例 ====================

session_store = SessionStore(config.CHAT_DB_PATH)

__all__ = ["session_store"]
