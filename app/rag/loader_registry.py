"""模块 8：文档加载器注册表 —— 按扩展名分发加载器。

.md/.txt  → TextLoader（纯文本直读）
.pdf      → MinerULoader（复杂版式/扫描件，云端 OCR）失败时自动降级 PyPDFLoader（本地）
.docx     → MinerULoader 失败时自动降级 Docx2txtLoader（本地）
其他格式  → 抛 UnsupportedFileTypeError
统一输出：list[langchain Document]（page_content + metadata）

健壮性：MinerU 是云端 API，网络抖动/token 失效不应阻塞入库——
降级策略保证"能用"优先，"高质量"次之。
"""

import logging
from pathlib import Path

from langchain_community.document_loaders import (
    Docx2txtLoader,
    PyPDFLoader,
    TextLoader,
)
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_mineru import MinerULoader

from app import config

logger = logging.getLogger(__name__)


class UnsupportedFileTypeError(Exception):
    """不支持的文件类型。"""


class MinerUWithFallbackLoader(BaseLoader):
    """MinerU 云端解析 + 本地兜底：云端失败（网络/token 问题）自动降级本地解析。

    云端服务不可用时自动切换到本地解析。
    """

    def __init__(self, path: Path, mode: str, ocr: bool):
        self.path = path
        self.mode = mode
        self.ocr = ocr

    def load(self) -> list[Document]:
        try:
            loader = MinerULoader(source=str(self.path), mode=self.mode, ocr=self.ocr)
            docs = loader.load()
            logger.info(f"MinerU 解析成功: {self.path.name} ({len(docs)} 页)")
            return docs
        except Exception as e:  # noqa: BLE001 —— 任何云端失败都降级
            logger.warning(f"MinerU 解析失败（{type(e).__name__}: {e}），降级本地解析")
            if self.path.suffix.lower() == ".pdf":
                return PyPDFLoader(str(self.path)).load()
            return Docx2txtLoader(str(self.path)).load()


class LoaderRegistry:
    """注册表/工厂：把"选加载器"的决策集中一处，新增格式只需加一个映射。"""

    def get_loader(self, path: Path) -> BaseLoader:
        suffix = path.suffix.lower()
        if suffix in (".md", ".txt"):
            return TextLoader(str(path), encoding="utf-8")
        if suffix == ".pdf":
            return self._get_pdf_loader(path)
        if suffix == ".docx":
            # Word 文本版式，无需 OCR；MinerU 失败降级本地 docx2txt
            return MinerUWithFallbackLoader(path, mode="flash", ocr=False)
        raise UnsupportedFileTypeError(
            f"不支持的文件类型: {suffix}（支持: {sorted(config.SUPPORTED_EXTENSIONS)}）"
        )

    def _get_pdf_loader(self, path: Path) -> BaseLoader:
        """PDF 按配置选解析器：mineru（复杂版式/扫描件）| pypdf（纯文本快速）。"""
        if config.PDF_MODE == "mineru":
            return MinerUWithFallbackLoader(path, mode="flash", ocr=True)
        return PyPDFLoader(str(path))

    def load(self, path: Path) -> list[Document]:
        """加载文件 → Document 列表。"""
        return self.get_loader(path).load()
