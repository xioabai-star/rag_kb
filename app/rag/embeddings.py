"""模块 2：文本向量化（DashScope text-embedding-v2，1536 维）。

移植自 RAG notebook 的 FixedDashScopeEmbeddings 实验验证过的方案：
- 查询（单条）：网络抖动 / API 限流(429) 时指数退避重试
- 入库（批量）：分批调用（16 条/批），每批带重试
"""

import logging
import time

from dashscope import TextEmbedding
from langchain_community.embeddings import DashScopeEmbeddings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3 #max_retries
BATCH_SIZE = 16 #batch_size


class FixedDashScopeEmbeddings(DashScopeEmbeddings):
    """覆写官方类的两个 embed 方法，加上重试与分批保护。"""

    def _call_with_retry(self, texts: list[str]) -> list[list[float]]:
        """调用 DashScope TextEmbedding API，失败指数退避重试。"""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = TextEmbedding.call(
                    model=self.model,
                    input=texts,
                    api_key=self.dashscope_api_key,
                    base_url=getattr(self, "base_url", None),
                )
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"DashScope embedding 失败: {resp.code} {resp.message}"
                    )
                # output.embeddings: [{"text_index": i, "embedding": [...]}, ...]
                return [item["embedding"] for item in resp.output["embeddings"]]
            except Exception as e:  # noqa: BLE001 —— 网络抖动/限流均需重试
                if attempt == MAX_RETRIES:
                    logger.error(f"embedding 调用最终失败: {e}")
                    raise
                wait = 2 ** attempt  # 1s → 2s → 4s 指数退避
                logger.warning(
                    f"embedding 调用失败({e})，{wait}s 后重试 ({attempt}/{MAX_RETRIES})"
                )
                time.sleep(wait)
        raise RuntimeError("unreachable")  # pragma: no cover

    def embed_query(self, text: str) -> list[float]:
        """单条查询向量化。"""
        return self._call_with_retry([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化：分批调用（16 条/批），避免单次请求过大触发限流。"""
        results: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            results.extend(self._call_with_retry(batch))
        return results
