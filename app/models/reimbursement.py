import logging

import aiosqlite
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

class ReimbursementStore:
    def __init__(self,db_path):
        self.db_path = db_path
        self.conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents= True, exist_ok= True)
        self.conn = await aiosqlite.connect(self.db_path)
        await self.conn.execute(_CREATE_SQL)
        await self.conn.commit()
        logger.info(f"reimbursements 表就绪：{self.db_path}")

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

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