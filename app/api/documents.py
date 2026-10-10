"""模块 14a：文档管理 API。

POST   /api/v1/documents/upload  上传（multipart，立即返回，后台异步入库）
GET    /api/v1/documents          文档列表
DELETE /api/v1/documents/{doc_id} 删除（Chroma 删块 + BM25 重建 + 删原件）
"""

import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile,Depends

from app import config
from app.rag.bm25_index import get_bm25_index
from app.rag.document_manager import DocumentManager
from app.rag.loader_registry import LoaderRegistry
from app.rag.vectorstore import get_vectorstore
from app.common.security import get_current_user
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(get_current_user)])

# 进程级单例：组装入库编排器（Chroma/BM25 单例复用）
document_manager = DocumentManager(
    registry=LoaderRegistry(),
    vectorstore=get_vectorstore(),
    bm25=get_bm25_index(),
    docs_db_path=config.DOCS_DB_PATH,
    upload_dir=config.UPLOAD_DIR,
)


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)) -> dict:
    """上传文档：校验扩展名 → 登记 + 后台入库 → 立即返回 processing 状态。"""
    filename = Path(file.filename or "").name
    ext = Path(filename).suffix.lower()
    if ext not in config.SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {ext or '(无扩展名)'}（支持: {sorted(config.SUPPORTED_EXTENSIONS)}）",
        )
    file_bytes = await file.read()
    record = await document_manager.ingest(filename, file_bytes)
    logger.info(f"收到上传: {filename} ({len(file_bytes)}B)")
    return asdict(record)


@router.get("")
async def list_documents() -> dict:
    """文档列表（读侧车 documents.json，含状态与块数）。"""
    return {"documents": [asdict(r) for r in document_manager.list_documents()]}


@router.delete("/{doc_id}")
async def delete_document(doc_id: str) -> dict:
    """删除文档：向量/索引/原件/登记四处同步清理。"""
    if not document_manager.get_document(doc_id):
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    await document_manager.delete(doc_id)
    return {"ok": True}
