"""模块 7：混合检索管线（组装稠密 + 稀疏 + 融合 + 精排）。

retrieve(query) 流程：
  Chroma 稠密 top N → BM25 稀疏 top N（doc_id 回查 Chroma 取原文/元数据）
  → RRF 融合 → 前 rerank_candidates 个 → CrossEncoder 精排 → top_k
任一阶段为空即返回 []（绝不硬凑）。

注意：本模块是同步函数（Chroma/BM25/模型均为同步库），
调用方（graph 检索节点）必须用 asyncio.to_thread 包裹，避免阻塞事件循环。
"""

from dataclasses import dataclass

from langchain_chroma import Chroma

from app import config
from app.rag.bm25_index import BM25Index
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.reranker import Reranker


@dataclass
class RetrievedChunk:
    """检索结果单元：原文 + 精排分数 + 来源元数据（供引用溯源）。"""

    chunk_id: str
    content: str
    score: float
    metadata: dict  # 含 doc_id / filename / header / chunk_index


@dataclass
class RetrievalConfig:
    """检索参数（默认值统一取自 config.RetrievalConfig——唯一事实源；
    实例可注入覆盖，测试时可传 cfg=RetrievalConfig(rerank_candidates=5) 快速跑）。"""

    dense_k: int = config.RetrievalConfig.dense_k
    bm25_k: int = config.RetrievalConfig.bm25_k
    rrf_k: int = config.RetrievalConfig.rrf_k
    rerank_candidates: int = config.RetrievalConfig.rerank_candidates
    final_top_k: int = config.RetrievalConfig.final_top_k


class HybridRetriever:
    """检索管线。组件通过依赖注入传入（可替换、可 mock 测试）。"""

    def __init__(
        self,
        vectorstore: Chroma,
        bm25: BM25Index,
        reranker: Reranker,
        cfg: RetrievalConfig | None = None,
    ):
        self.vectorstore = vectorstore
        self.bm25 = bm25
        self.reranker = reranker
        self.cfg = cfg or RetrievalConfig()

    def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievedChunk]:
        """完整混合检索：稠密 → 稀疏 → RRF 融合 → 精排。"""
        final_k = top_k or self.cfg.final_top_k

        # 1. 稠密检索（语义）
        dense_docs = self.vectorstore.similarity_search(query, k=self.cfg.dense_k)
        dense_list = [self._to_item(d) for d in dense_docs]

        # 2. 稀疏检索（关键词精确匹配）
        bm25_hits = self.bm25.search(query, k=self.cfg.bm25_k)
        sparse_list = self._fetch_by_ids([doc_id for doc_id, _ in bm25_hits])

        # 3. RRF 融合（两路都有才融合，单路则直接用那一路）
        if dense_list and sparse_list:
            fused = reciprocal_rank_fusion([dense_list, sparse_list], k=self.cfg.rrf_k)
        else:
            fused = dense_list or sparse_list

        candidates = fused[: self.cfg.rerank_candidates]
        if not candidates:
            return []

        # 4. 精排（CrossEncoder 对候选精确打分）
        ranked = self.reranker.rerank(query, candidates, top_k=final_k)

        return [
            RetrievedChunk(
                chunk_id=item["id"],
                content=item["content"],
                score=item.get("score", 0.0),
                metadata=item["metadata"],
            )
            for item in ranked
        ]

    # ---------- 内部工具 ----------

    @staticmethod
    def _to_item(doc) -> dict:
        """LangChain Document → 统一 dict（id 从 metadata 取，入库时写入）。"""
        return {
            "id": doc.metadata.get("id") or doc.id,
            "content": doc.page_content,
            "metadata": doc.metadata,
        }

    def _fetch_by_ids(self, ids: list[str]) -> list[dict]:
        """BM25 只返回 doc_id，回查 Chroma 取原文与元数据（保持输入顺序）。"""
        if not ids:
            return []
        data = self.vectorstore.get(ids=ids, include=["documents", "metadatas"])
        by_id = {
            doc_id: (content, meta)
            for doc_id, content, meta in zip(
                data["ids"], data["documents"], data["metadatas"]
            )
        }
        return [
            {"id": doc_id, "content": by_id[doc_id][0], "metadata": by_id[doc_id][1]}
            for doc_id in ids
            if doc_id in by_id
        ]
