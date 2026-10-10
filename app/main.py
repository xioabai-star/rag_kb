"""模块 14d：FastAPI 入口 —— 装配路由、中间件、生命周期与前端静态托管。

启动：uv run uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload
注意：load_dotenv() 必须在 import 任何 app 模块之前（config/agent 构造时读 env）。
"""

# 先加载 .env（密钥注入环境变量），再 import 业务模块 —— 顺序不可调换
# 坑：user_store 曾插在 load_dotenv() 之前，import 链提前执行了 config 模块，
# 导致 AMAP_KEY / JWT_SECRET 全部读成空或默认值（JWT 用默认密钥签名 = 安全隐患）
from dotenv import load_dotenv

load_dotenv()

from app.models.user import user_store

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app import config  # noqa: E402
from app.api import chat, documents, sessions, auth  # noqa: E402
from app.common.logger import logger  # noqa: E402
from app.models.session import session_store  # noqa: E402
from app.rag.agent import rag_agent  # noqa: E402
from app.models.reimbursement import reimbursement_store # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：会话表 → RAGAgent（checkpointer + 检索管线 + 图）。
    关闭：反向释放资源。"""
    await session_store.init()
    await user_store.init()
    await reimbursement_store.init()
    await rag_agent.init()
    yield
    await rag_agent.close()
    await user_store.close()
    await reimbursement_store.close()
    await session_store.close()


app = FastAPI(title="RAG 企业知识库问答系统", lifespan=lifespan)

# CORS：开发期前端（Vite :5173）跨域访问；生产同源无需
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 业务路由（注册在前，优先匹配 /api/v1/*）
app.include_router(documents.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(sessions.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")

@app.get("/api/v1/health")
async def health() -> dict:
    """服务健康检查。"""
    return {"status": "ok"}


# 生产模式：Vue 构建产物挂载到根路径（build 后出现 dist/ 才生效）
if config.STATIC_DIR.exists():
    app.mount(
        "/", StaticFiles(directory=str(config.STATIC_DIR), html=True), name="static"
    )
    logger.info(f"已挂载前端静态资源: {config.STATIC_DIR}")
