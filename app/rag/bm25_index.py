"""模块 4：BM25 稀疏检索索引（bm25s + jieba 中文分词）。

与 Chroma（稠密）互补：按关键词精确匹配，对专有名词/编号/术语效果好。
一致性策略：索引不自建语料，而是从 Chroma 全量重建（唯一事实源 = Chroma），
写临时文件后原子替换；重建期间检索读旧索引，不中断服务。
"""

import json
import logging
import os
import threading
from pathlib import Path

import bm25s
import jieba
from langchain_chroma import Chroma

from app import config

logger = logging.getLogger(__name__)

# 保存到磁盘的文件名（bm25s 本体文件 + 我们的 id 映射）
# 注意：bm25s 0.3.x 用 data.csc.index.npy 等一组文件（0.2.x 是单个 index.bin），
# load 的守卫必须与实际文件名一致，否则索引永远"加载不上"（历史踩坑）
_DATA_FILE = "data.csc.index.npy"
_IDS_FILE = "ids.json"


# ---- bm25s 0.3.x 适配说明 ----
# 0.3.x 移除了 tokenize(tokenizer=) 参数；Tokenizer 类 + return_as='ids' 时 vocab 键是 int，
# save() 用 orjson 序列化会报 "Dict key must be str"。最稳方案：自己用 jieba 分词后
# 传 list[list[str]] 给 BM25.index()（内部自建 vocab，键为 str），save/load 均正常。
def _jieba_tokenize(text: str) -> list[str]:
    """jieba 中文分词：'出差报销需要填写报销单' → ['出差','报销',...]。"""
    return [w for w in jieba.lcut(text) if w.strip()]


class BM25Index:
    """BM25 稀疏索引：分词 → 统计词频/逆文档频率 → 打分检索。"""

    def __init__(self, index_path: Path):
        self.index_path = index_path
        self.bm25: bm25s.BM25 | None = None
        self.corpus: list[dict] = []      # [{"id", "content"}]，位置即索引下标
        # 重建锁：并发入库时多个后台任务同时 rebuild 会写同一临时文件（Windows 文件锁冲突）
        self._rebuild_lock = threading.Lock()

    # ---------- 分词 ----------

    @staticmethod
    def tokenize(text: str) -> list[str]:
        """jieba 中文分词（供外部/测试使用）。"""
        return _jieba_tokenize(text)

    # ---------- 构建 ----------

    def build(self, corpus: list[dict]) -> None:
        """用新的语料全量重建索引（build 完成后才替换引用，检索期间读旧索引）。"""
        if not corpus:
            self.bm25 = None
            self.corpus = []
            return
        tokenized = [self.tokenize(item["content"]) for item in corpus]
        new_bm25 = bm25s.BM25(k1=1.5, b=0.75)
        new_bm25.index(tokenized, show_progress=False)
        # 替换引用（Python 赋值原子，并发检索读到旧或新，绝无中间态）
        self.bm25 = new_bm25
        self.corpus = corpus

    def rebuild_from_vectorstore(self, collection: Chroma) -> None:
        """从 Chroma 全量重建（唯一事实源）——增删文档后由 DocumentManager 调用。"""
        with self._rebuild_lock:  # 串行化重建（临时文件与索引文件互不踩踏）
            data = collection.get(include=["documents", "metadatas"])
            ids, docs, metas = data["ids"], data["documents"], data["metadatas"]
            corpus = [
                {"id": doc_id, "content": content, "metadata": meta}
                for doc_id, content, meta in zip(ids, docs, metas)
            ]
            self.build(corpus)
            self.save()
        logger.info(f"BM25 全量重建完成：{len(corpus)} 块")

    # ---------- 检索 ----------

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        """按关键词打分检索，返回 [(doc_id, score)] 降序。"""
        if self.bm25 is None or not self.corpus:
            return []
        # bm25s 0.3.x：k 必须 ≤ 语料数（否则抛 ValueError）；
        # load_corpus=True 时结果元素是 {"id", "text"} dict（新版），也可能仍是 int 索引
        results, scores = self.bm25.retrieve(
            [self.tokenize(query)],
            k=min(k, len(self.corpus)),
            show_progress=False,
        )
        out: list[tuple[str, float]] = []
        for item, score in zip(results[0], scores[0]):
            if float(score) <= 0:  # 0.3.x 返回全部索引，无匹配分数为 0
                continue
            idx = item["id"] if isinstance(item, dict) else item
            doc_id = self.corpus[idx]["id"]
            out.append((doc_id, float(score)))
        return out

    # ---------- 持久化 ----------

    def save(self) -> None:
        """写 .tmp_* 临时文件后逐个原子替换正式名（文件级替换）。

        Windows 注意：os.rename 目标已存在抛 FileExistsError，目录替换到已存在
        目录抛 PermissionError（[WinError 5]）—— 所以不用目录级 rename，
        而是文件级 os.replace（覆盖式原子改名，跨平台可靠）。
        """
        self.index_path.mkdir(parents=True, exist_ok=True)
        if self.bm25 is not None:
            # 每个文件写成 .tmp_<原名>（save 支持定制全部文件名）
            self.bm25.save(
                self.index_path,
                corpus=[c["content"] for c in self.corpus],
                data_name=".tmp_data.csc.index.npy",
                indices_name=".tmp_indices.csc.index.npy",
                indptr_name=".tmp_indptr.csc.index.npy",
                vocab_name=".tmp_vocab.index.json",
                params_name=".tmp_params.index.json",
                nnoc_name=".tmp_nonoccurrence_array.index.npy",
                corpus_name=".tmp_corpus.jsonl",
            )
            # 按实际生成的 .tmp_* 文件逐个原子替换（文件集因版本/参数而异，不硬编码）
            for tmp in self.index_path.glob(".tmp_*"):
                if tmp.name.startswith(f".tmp_{_IDS_FILE}"):
                    continue  # id 映射文件单独处理（下面）
                os.replace(tmp, self.index_path / tmp.name[len(".tmp_"):])
        # 单独存 id 列表（bm25s 的 corpus 只有文本，id 映射我们自己管）
        ids_file = self.index_path / _IDS_FILE
        tmp_ids = self.index_path / f".tmp_{_IDS_FILE}"
        tmp_ids.write_text(
            json.dumps([{"id": c["id"], "metadata": c.get("metadata", {})}
                        for c in self.corpus], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        os.replace(tmp_ids, ids_file)

    def load(self) -> bool:
        """从磁盘加载索引，成功返回 True。"""
        data_file = self.index_path / _DATA_FILE
        ids_file = self.index_path / _IDS_FILE
        if not (data_file.exists() and ids_file.exists()):
            return False
        # save_dir 传目录（0.3.x load 第一参数是目录，不是文件路径）
        self.bm25 = bm25s.BM25.load(
            self.index_path, load_corpus=True, show_progress=False
        )
        self.corpus = [
            {"id": item["id"], "content": content, "metadata": item.get("metadata", {})}
            for item, content in zip(
                json.loads(ids_file.read_text(encoding="utf-8")),
                self.bm25.corpus,
            )
        ]
        logger.info(f"BM25 索引已加载：{len(self.corpus)} 块")
        return True

    # ---------- 查询 ----------

    def corpus_size(self) -> int:
        return len(self.corpus)


# ==================== 进程级单例 ====================

_lock = threading.Lock()
_instance: BM25Index | None = None


def get_bm25_index() -> BM25Index:
    """进程级单例：懒加载（首次访问时尝试从磁盘加载）。"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                index = BM25Index(config.DATA_DIR / "bm25")
                index.load()  # 磁盘没有则空索引，首次入库时全量重建
                _instance = index
    return _instance
