"""模块 3：Chroma 向量库（稠密检索）。

存储：每个文档块 = 唯一 id + 1024 维向量 + 原文 + metadata（doc_id/filename/header）
查询：similarity_search 按语义相似度召回
单例：全进程共享一个实例（初始化要加载磁盘目录，避免重复开销）
"""

from langchain_chroma import Chroma

from app import config
from app.rag.embeddings import FixedDashScopeEmbeddings

COLLECTION_NAME = "rag_kb"

_vectorstore: Chroma | None = None


def get_vectorstore() -> Chroma:
    """进程级单例：获取（或创建）Chroma 向量库实例。"""
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=FixedDashScopeEmbeddings(),#覆写（Fixed）官方类DashEmbeddings
            persist_directory=str(config.DATA_DIR / "chroma"),
        )
    return _vectorstore
