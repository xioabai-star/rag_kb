import logging
from uuid import uuid4
import json
import aiosqlite
import asyncio

from app import config

logger = logging.getLogger(__name__)

# 报销单业务表（独立于会话/用户表，模拟"企业业务系统数据"，供 Agent 工具查询）
_CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS reimbursements (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      TEXT NOT NULL,          -- 归属人（= users.user_id，与数据隔离同一思路）
        expense_type TEXT NOT NULL,          -- 费用类型：差旅 / 办公用品 / 业务招待 ...
        amount       REAL NOT NULL,          -- 报销金额（元）
        status       TEXT NOT NULL,          -- 状态机：审批中 / 财务审核中 / 已打款 / 被驳回
        submitted_at TEXT NOT NULL           -- 提交时间（yyyy-mm-dd HH:MM:SS）
    )
"""

_CREATE_TASKS_SQL = """
    CREATE TABLE IF NOT EXISTS reimbursement_tasks (
        task_id TEXT PRIMARY KEY NOT NULL,
        user_id TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'collecting_info'
            CHECK (status IN (
                'collecting_info', 'needs_review', 'awaiting_confirmation',
                'submitted', 'cancelled', 'failed'
            )),
        draft_json TEXT NOT NULL DEFAULT '{}',
        draft_revision INTEGER NOT NULL DEFAULT 1 CHECK (draft_revision > 0),
        reimbursement_id INTEGER UNIQUE
    )
"""

class ReimbursementStore:
    def __init__(self,db_path):
        self.db_path = db_path
        self.conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents= True, exist_ok= True)
        self.conn = await aiosqlite.connect(self.db_path)
        await self.conn.execute(_CREATE_SQL)
        await self.conn.execute(_CREATE_TASKS_SQL)
        await self.conn.commit()
        logger.info(f"reimbursements 表就绪：{self.db_path}")

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def create_draft(
            self,user_id: str,
            thread_id: str,
            draft: dict
    ) -> str:
        """保存保小草稿并返回任务ID"""
        task_id = uuid4().hex
        draft_json = json.dumps(draft, ensure_ascii= False)
        async with self._write_lock:
            try:
                await self.conn.execute(
                    """
                    INSERT INTO reimbursement_tasks(
                        task_id,
                        user_id,
                        thread_id,
                        draft_json
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (task_id, user_id, thread_id, draft_json)
                )
                await self.conn.commit()
            except(Exception, asyncio.CancelledError):
                await self.conn.rollback()
                raise
        return task_id


    async def get_draft(self,user_id: str, task_id: str) -> dict | None:
        """读取指定用户的报销草稿"""
        async with self.conn.execute(
            """
            SELECT thread_id, status, draft_json, draft_revision, reimbursement_id
            FROM reimbursement_tasks
            WHERE task_id = ? AND user_id = ?
            """,
                (task_id, user_id)
        ) as cursor:
            row = await cursor.fetchone()

            if row is None:
                return None

            thread_id, status, draft_json, draft_revision, reimbursement_id = row
            return {
                "task_id":task_id,
                "thread_id":thread_id,
                "status":status,
                "draft":json.loads(draft_json),
                "draft_revision":draft_revision,
                "reimbursement_id":reimbursement_id,
            }



    async def list_by_user(self, user_id: str, limit: int = 5) -> list[dict]:
        """查某某用户最近的报销单（工具节点用；倒序取最近的limit条。"""
        cur = await self.conn.execute(
            "SELECT id, expense_type, amount, status, submitted_at"
            " FROM reimbursements WHERE user_id = ?"
            " ORDER BY submitted_at DESC LIMIT ?",
            (user_id, limit)
        )
        rows = await cur.fetchall()
        # 翻译：元组 → 带名字的字典
        result = []
        for row in rows:
            result.append({
                "id": row[0],
                "expense_type": row[1],
                "amount": row[2],
                "status": row[3],
                "submitted_at": row[4],
            })
        return result

reimbursement_store = ReimbursementStore(config.CHAT_DB_PATH)

__all__ = ["reimbursement_store"]