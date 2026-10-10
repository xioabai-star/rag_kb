"""模块 1：全局配置。

集中管理所有路径与参数，其他模块从这里取值，不散落写死。
路径全部由 BASE_DIR 派生（绝对路径、自包含），不依赖"当前工作目录"。
"""

import os
from pathlib import Path

# ==================== 路径 ====================

# 项目根目录：config.py 在 app/ 下，往上跳 1 级即为项目根
# （无论从哪个目录启动/用什么 IDE 运行，此值都正确）
BASE_DIR = Path(__file__).resolve().parents[1]

# 运行时数据目录（被 .gitignore 排除）
DB_DIR = BASE_DIR / "db"          # 会话数据库、文档原件、文档登记表
DATA_DIR = BASE_DIR / "data"      # Chroma 向量目录、BM25 索引目录

# 上传文档原件存档
UPLOAD_DIR = DB_DIR / "docs"
# 文档登记表（侧车）：记录"有哪些文档、状态、块数"
DOCS_DB_PATH = DB_DIR / "documents.json"
# 会话数据库（checkpointer + sessions 业务表）
CHAT_DB_PATH = DB_DIR / "chat.db"

# 前端构建产物（Vue build 后拷贝/挂载至此）
STATIC_DIR = BASE_DIR / "app" / "static" / "dist"


# ==================== 模型 ====================

# 本地精排模型路径：默认指向已有模型，可用环境变量 RERANKER_MODEL_PATH 覆盖
RERANKER_MODEL_PATH = os.environ.get(
    "RERANKER_MODEL_PATH", "D:/code/ai_demo/models/Qwen3-Reranker-0.6B"
)

# ONNX 精排后端（加速 ~4.7x，分数几乎无损，见 scripts/probe_onnx_smoke.py）：
# 目录 + 开关，环境变量可覆盖；目录不存在时 get_reranker 自动回退 torch 版
ONNX_RERANKER_DIR = os.environ.get(
    "ONNX_RERANKER_DIR", "D:/code/ai_demo/models/qwen3-reranker-0.6b-onnx-fp32"
)
RERANK_BACKEND = os.environ.get("RERANK_BACKEND", "onnx")  # "onnx" | "torch"


# ==================== 检索参数 ====================

class RetrievalConfig:
    """混合检索管线参数（模块 7：hybrid_retriever 使用）"""

    dense_k: int = 20             # 稠密检索（Chroma）召回条数
    bm25_k: int = 15              # 稀疏检索（BM25）召回条数
    rrf_k: int = 60               # RRF 融合平滑常数
    rerank_candidates: int = 15   # 送入精排的候选数（RRF 融合后取前 N；CPU 精排昂贵，15 条足够，实际生效值见 hybrid_retriever.RetrievalConfig）
    final_top_k: int = 5          # 最终返回给 LLM 的块数


# ==================== 生成模型 ====================

GENERATE_MODEL = "deepseek-chat"  # DeepSeek 生成模型（key 在 .env）
TEMPERATURE = 0.3                 # 生成温度（偏保守，减少幻觉）

# 多轮对话上下文窗口：保留最近 N 轮消息（滑动窗口剪枝）
HISTORY_WINDOW = 6

# ==================== 文档解析 ====================

# PDF 解析模式："mineru"（复杂版式/扫描件，云端 OCR）| "pypdf"（纯文本快速）
PDF_MODE = os.environ.get("PDF_MODE", "mineru")

# 支持的文档扩展名
SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}


# ==================== 认证 ====================
# JWT 签名密钥：必须由 .env 提供（泄漏 = 任何人都能伪造 token）
JWT_SECRET = os.environ.get("JWT_SECRET","dev-secret-change-me")
JWT_ALGORITHM = "HS256" # 签名算法（与签发/验证一致）
JWT_EXPIRE_HOURS = 24 # token 过期时间（小时）

# ==================== 外部服务 ====================
# 高德地图 Web服务 Key：从 .env 读，不提交 git；泄漏会被盗刷免费额度
AMAP_KEY = os.environ.get("AMAP_KEY","")