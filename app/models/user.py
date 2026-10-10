"""模块 15：用户表（users）。

  登录鉴权双件套之一：users 表存账号与密码哈希（bcrypt）；
  JWT 无状态，服务端不存 token。
  """
import logging
import time
import uuid
import aiosqlite

from app import config

logger = logging.getLogger(__name__) #

_CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'employee',
    created_at    TEXT NOT NULL
  )
"""

class UserStore:
    """users 表读写（aiosqlite，与 sessions/checkpointer 共用 chat.db）。"""
    def __init__(self,db_path):
        self.db_path = db_path
        self.conn : aiosqlite.Connection | None = None

    async def init(self) ->None:      # ← 概念 3：启动时建表（幂等）
        self.db_path.parent.mkdir(parents = True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.db_path)
        await self.conn.execute(_CREATE_SQL) # IF NOT EXISTS：重复跑不报错
        await self.conn.commit()
        logger.info(f"users 表就绪：{self.db_path}")

    async  def close(self) -> None:     # 关闭连接，释放资源
        if self.conn:
            await self.conn.close()

    async def create(self,username: str, password_hash: str, role: str = "employee") ->dict:
        """注册：插一条用户（密码已是哈希，调用方负责加密）。"""
        user_id = f"u_{uuid.uuid4().hex[:12]}"  #生成唯一编号
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        await self.conn.execute(
            "INSERT INTO USERS(user_id, username, password_hash, role, created_at)"
            "VALUES(?, ?, ?, ?, ?)",
            (user_id, username, password_hash, role, now)
        )

        await self.conn.commit()
        return {
            "user_id": user_id,
            "username": username,
            "role": role,
            "created_at": now
        }

    async def get_by_username(self,username: str) -> dict | None:
        """按用户名查（登录校验用）；不存在返回 None。"""
        cur = await self.conn.execute(
            "SELECT user_id, username, password_hash, role, created_at"
            " FROM users WHERE username = ?",
            (username,)
        )
        row = await cur.fetchone()

        if not row:
            return None

        return{
            "user_id": row[0],
            "username": row[1],
            "password_hash": row[2],
            "role": row[3],
            "created_at": row[4]
        }

 # ==================== 进程级单例 ====================   ← 概念 4

user_store = UserStore(config.CHAT_DB_PATH)

__all__ = ["user_store"]
