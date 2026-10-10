"""模块 17：认证 API。

 POST /api/v1/auth/register  注册（查重 → 存 bcrypt 指纹）
 POST /api/v1/auth/login     登录（验指纹 → 签发 JWT）——表单格式，兼容 Swagger Authorize
 GET  /api/v1/auth/me        当前用户（验证 token 有效性 + 拿身份）
 """
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.common.security import (
      create_token,
      get_current_user,
      hash_password,
      verify_password,
)
from app.models.user import user_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "employee"  # 演示简化：开放注册可指定角色；生产应默认 employee 且仅管理员可提权

@router.post("/register")
async def register(req: RegisterRequest) -> dict:
    """注册：查重 → 哈希 → 入库。不返回 token（与登录职责分离）。"""
    if await user_store.get_by_username(req.username):
        raise HTTPException(status_code= 400, detail="用户名已存在")
    password_hash = hash_password(req.password)
    user = await user_store.create(req.username,password_hash,req.role)
    logger.info(f"新用户注册: {req.username} (role={req.role})")
    return user

@router.post("/login")
async def login(form: OAuth2PasswordRequestForm = Depends()) -> dict:
    """登录：验证用户名密码 → 签发 JWT。

    请求体是表单而非 JSON：兼容 Swagger UI 的 Authorize 按钮（OAuth2 密码模式）。
    失败统一 401「用户名或密码错误」：不区分错在哪个，防用户名枚举。
    """
    user = await user_store.get_by_username(form.username)
    if not user or not verify_password(form.password, user["password_hash"]):
        raise HTTPException(
            status_code= 401,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_token(user)
    return{
        "access_token": token,
        "token_type": "bearer",
        "user":{
            "user_id": user["user_id"],
            "username": user["username"],
            "role": user["role"],
        },
    }

@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)) ->dict:
    """当前用户：保镖验 token 后把身份交进来（无状态，不查库）。

    前端启动时调它验证登录态；token 无效/过期 → 401。
    """
    return {
        "user_id": current_user["sub"],
        "username": current_user["username"],
        "role": current_user["role"],
    }