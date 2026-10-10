"""
模块 16：认证工具 —— 密码哈希 + JWT 签发/验证 + 当前用户依赖。
登录鉴权双件套之二：security 提供「密码指纹」与「通行证」的制造和查验；
auth 路由（下一步）负责把两者组合成注册/登录接口。
"""

import logging
from datetime import datetime, timedelta , timezone


import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import config

logger = logging.getLogger(__name__)


#自动从请求头取 Authorization: Bearer <token>
_bearer = HTTPBearer(auto_error=False)

# ==================== 密码哈希（概念 A） ====================
def hash_password(password: str) -> str:
    """密码 → bcrypt 指纹（自动加盐，不可逆）。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, password_hash: str) ->bool:
    """登录校验： 把输入密码算成指纹，与库里存的比对。"""
    return bcrypt.checkpw(password.encode("utf-8"),password_hash.encode("utf-8"))

# ==================== JWT 签发与验证（概念 B、C） ====================
def create_token(user: dict) ->str:
    """签发通行证：payload = 身份声明 + 过期时间，用 SECRET 签名。"""
    payload = {
        "sub": user["user_id"],
        "username": user["username"],
        "role": user["role"],
        "exp": datetime.now(timezone.utc) + timedelta(hours=config.JWT_EXPIRE_HOURS)
    }
    return jwt.encode(payload,config.JWT_SECRET,algorithm=config.JWT_ALGORITHM)

def decode_token(token: str) ->dict:
    """查验通行证：验签 + 自动查过期；失败抛 401。"""
    try:
        return jwt.decode(token,config.JWT_SECRET,algorithms=[config.JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code= status.HTTP_401_UNAUTHORIZED,
            detail="token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"}
        )

 # ==================== 当前用户依赖（概念 E） ====================
def get_current_user(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """路由保镖：取 token → 查验 → 返回身份声明（无状态，不查库）。
    任何路由写上 user: dict = Depends(get_current_user) 即被保护。
    """
    if credentials is None:
        raise HTTPException(
            status_code= status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return decode_token(credentials.credentials)