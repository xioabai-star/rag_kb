"""模块 16 配套：给 chat.db 种演示用业务数据（reimbursements 报销单表）。

用途：为报销单查询工具提供演示数据。
本脚本创建业务数据表 reimbursements 并给 admin 账号插入 3 条演示报销单
（审批中 / 已打款 / 被驳回），状态与《费用报销制度》流程对应：
  提交申请 → 部门负责人审批 → 财务审核（3 个工作日）→ 出纳付款（5 个工作日）

用法：uv run python scripts/seed_demo_business_data.py
幂等：重复执行会先清掉该账号旧数据再插，不会越积越多。
注意：需要 users 表里已存在 admin（先在界面注册 / 或用过登录接口）。
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402

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

# 演示数据：3 条报销单覆盖 3 种状态（配合知识库《费用报销制度》口径）
_SEED_ROWS = [
    # 状态 1：审批中 —— 差旅报销（可演示"查进度 → 引导看 30 天时限"）
    ("差旅", 1860.50, "审批中", "2026-08-26 09:12:00"),
    # 状态 2：已打款 —— 办公用品（流程走完的完整示例）
    ("办公用品", 320.00, "已打款", "2026-07-03 14:30:00"),
    # 状态 3：被驳回 —— 可演示"为什么被驳回"（对照知识库发票抬头要求）
    ("业务招待", 580.00, "被驳回", "2026-06-18 10:05:00"),
]


async def main() -> None:
    db = config.CHAT_DB_PATH
    db.parent.mkdir(parents=True, exist_ok=True)

    import aiosqlite

    conn = await aiosqlite.connect(db)
    try:
        await conn.execute(_CREATE_SQL)

        # admin 的 user_id（users 表里现查，不写死）
        cur = await conn.execute(
            "SELECT user_id FROM users WHERE username = ?", ("admin",)
        )
        row = await cur.fetchone()
        if row is None:
            print("❌ users 表里还没有 admin 账号 —— 请先在前端注册/登录 admin，再跑本脚本")
            return
        admin_id = row[0]

        # 幂等：先清掉 admin 旧演示数据再插入
        await conn.execute("DELETE FROM reimbursements WHERE user_id = ?", (admin_id,))
        await conn.executemany(
            "INSERT INTO reimbursements"
            " (user_id, expense_type, amount, status, submitted_at)"
            " VALUES (?, ?, ?, ?, ?)",
            [(admin_id, *r) for r in _SEED_ROWS],
        )
        await conn.commit()

        cur = await conn.execute(
            "SELECT id, expense_type, amount, status, submitted_at"
            " FROM reimbursements WHERE user_id = ? ORDER BY id",
            (admin_id,),
        )
        print(f"✅ reimbursements 表就绪，admin（{admin_id}）现有报销单：")
        for r in await cur.fetchall():
            print(f"   #{r[0]}  {r[1]}  ¥{r[2]:.2f}  [{r[3]}]  {r[4]}")
    finally:
        await conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
