"""模块 10：文档入库编排与文档管理（DocumentManager）。

职责：
1. 入库（ingest）：落盘 → 登记 processing → 后台任务执行
   加载 → 分块 → 写 Chroma → 重建 BM25 → 状态 ready；失败回滚 → failed
2. 删除（delete）：Chroma 按 doc_id 过滤删 → 重建 BM25 → 删登记与原件
3. 列表（list_documents）：读侧车 documents.json（文档级信息，Chroma 只管块）

核心原则：
- 唯一事实源 = Chroma collection，所有写操作必须经过本类（禁绕过直写）
- 增删文档后同步全量重建 BM25，保证双索引永远一致
- 侧车 JSON 用线程锁保护单写者（规模小，文件即数据库）
"""

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

from langchain_chroma import Chroma

from app import config
from app.rag.bm25_index import BM25Index
from app.rag.chunking import split_documents
from app.rag.loader_registry import LoaderRegistry, UnsupportedFileTypeError

logger = logging.getLogger(__name__)


@dataclass
class DocumentRecord:
    """文档登记记录（侧车 documents.json 的一行）。"""

    doc_id: str
    filename: str
    size: int
    status: str                 # "processing" | "ready" | "failed"
    error: str | None = None
    uploaded_at: str = ""
    chunk_count: int = 0


class DocumentManager:
    """入库编排器：上传 → 后台异步处理 → 状态跟踪；支持删除与列表。"""

    def __init__(
        self,
        registry: LoaderRegistry,
        vectorstore: Chroma,
        bm25: BM25Index,
        docs_db_path: Path,
        upload_dir: Path,
    ):
        self.registry = registry
        self.vectorstore = vectorstore
        self.bm25 = bm25
        self.docs_db_path = docs_db_path
        self.upload_dir = upload_dir
        self._lock = threading.Lock()  # 侧车单写者保护
        self._tasks: dict[str, asyncio.Task] = {}  # 后台任务跟踪

    # ==================== 入库 ====================

    async def ingest(self, filename: str, file_bytes: bytes) -> DocumentRecord:
        """上传入口：落盘 + 登记 processing + 后台任务处理，立即返回。"""
        # ① 生成 doc_id + 原件落盘（Path(filename).name 防路径穿越）
        safe_name = Path(filename).name
        doc_id = f"doc_{uuid.uuid4().hex[:8]}"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.upload_dir / f"{doc_id}_{safe_name}"
        file_path.write_bytes(file_bytes)

        # ② 登记 processing
        record = DocumentRecord(
            doc_id=doc_id,
            filename=safe_name,
            size=len(file_bytes),
            status="processing",
            uploaded_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._save_record(record)

        # ③ 后台任务执行完整入库流程（MinerU 解析 PDF 分钟级，不能阻塞 HTTP 响应）
        task = asyncio.create_task(self._process_document(record, file_path))
        self._tasks[doc_id] = task
        logger.info(f"文档 {safe_name} 已登记，后台入库中 (doc_id={doc_id})")
        return record

    async def _process_document(
        self, record: DocumentRecord, file_path: Path
    ) -> None:
        """后台入库：加载 → 分块 → 写 Chroma → 重建 BM25。失败回滚。"""
        try:
            file_type = file_path.suffix.lower()
            # 加载（同步库，放线程池避免阻塞事件循环）
            raw_docs = await asyncio.to_thread(self.registry.load, file_path)
            if not raw_docs:
                raise RuntimeError("文档解析结果为空")

            # 分块（含标题回填、赋 id、打 metadata）
            chunks = split_documents(
                raw_docs, file_type, record.doc_id, record.filename
            )
            if not chunks:
                raise RuntimeError("文档分块结果为空")

            # 写 Chroma（embedding 分批带重试在 FixedDashScopeEmbeddings 内部）
            await asyncio.to_thread(
                self.vectorstore.add_documents,
                chunks,
                ids=[c.id for c in chunks],
            )
            # 重建 BM25（唯一事实源 = Chroma，全量重建保证一致）
            await asyncio.to_thread(self.bm25.rebuild_from_vectorstore, self.vectorstore)

            record.status = "ready"
            record.chunk_count = len(chunks)
            record.error = None
            self._save_record(record)  # 成功也要写回侧车（前端轮询状态依赖它）
            logger.info(f"文档 {record.filename} 入库完成：{len(chunks)} 块")
        except UnsupportedFileTypeError as e:
            await self._fail_and_rollback(record, str(e))
        except Exception as e:  # noqa: BLE001 —— 任何失败都要回滚并标记
            logger.exception(f"文档 {record.filename} 入库失败")
            await self._fail_and_rollback(record, str(e))
        finally:
            self._tasks.pop(record.doc_id, None)

    async def _fail_and_rollback(self, record: DocumentRecord, error: str) -> None:
        """失败处理：回滚已写入的向量（不留半成品，保证 Chroma/BM25 一致）。"""
        try:
            await asyncio.to_thread(self._delete_by_doc_id, record.doc_id)
        except Exception:  # noqa: BLE001 —— 回滚本身失败也要把状态标记好
            logger.exception("回滚删除向量失败")
        record.status = "failed"
        record.error = error
        self._save_record(record)
        logger.error(f"文档 {record.filename} 标记 failed: {error}")

    # ==================== 删除 ====================

    async def delete(self, doc_id: str) -> None:
        """用户点击删除按钮时候的操作。
        删除文档：Chroma 按 doc_id 过滤删块 → 重建 BM25 → 删登记 + 删原件。"""
        # 取消未完成的后台任务（避免入库中删除产生竞态）
        task = self._tasks.pop(doc_id, None)
        if task:
            task.cancel()

        await asyncio.to_thread(self._delete_by_doc_id, doc_id)
        await asyncio.to_thread(self.bm25.rebuild_from_vectorstore, self.vectorstore)

        # 删登记记录与原件
        with self._lock:
            records = self._read_records_locked()
            records = [r for r in records if r["doc_id"] != doc_id]
            self._write_records_locked(records)
        for f in self.upload_dir.glob(f"{doc_id}_*"):#根据doc_id查找文档。
            f.unlink(missing_ok=True)#删除文件
        logger.info(f"文档已删除 (doc_id={doc_id})")

    def _delete_by_doc_id(self, doc_id: str) -> None:
        """按 doc_id 过滤删除向量块。

        注意：langchain_chroma 的 delete() 只接受 ids（无 where 参数），
        必须先用 get(where=...) 查出该文档的全部 chunk id 再按 id 删。
        """
        data = self.vectorstore.get(where={"doc_id": doc_id}, include=[])
        ids = data["ids"]
        if ids:
            self.vectorstore.delete(ids)

    # ==================== 列表/查询 ====================

    def list_documents(self) -> list[DocumentRecord]:
        with self._lock:
            return [DocumentRecord(**r) for r in self._read_records_locked()]

    def get_document(self, doc_id: str) -> DocumentRecord | None:
        with self._lock:
            for r in self._read_records_locked():
                if r["doc_id"] == doc_id:
                    return DocumentRecord(**r)
        return None

    # ==================== 侧车（documents.json） ====================

    def _save_record(self, record: DocumentRecord) -> None:
        with self._lock:
            records = self._read_records_locked() #读
            records = [r for r in records if r["doc_id"] != record.doc_id] #改
            records.append(asdict(record))
            self._write_records_locked(records)  #写

    def _read_records_locked(self) -> list[dict]:
        if not self.docs_db_path.exists():
            return []
        return json.loads(self.docs_db_path.read_text(encoding="utf-8"))

    def _write_records_locked(self, records: list[dict]) -> None:
        self.docs_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.docs_db_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8"
        )
