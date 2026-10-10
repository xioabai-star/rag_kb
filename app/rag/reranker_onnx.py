"""ONNX 精排后端：与 Reranker 同接口，内部用 ONNX Runtime 跑自导出 fp32 模型。

动机：本地 CPU 上 torch 46.6s/14 对 → ONNX 10.0s/14 对（≈4.7x），分数几乎无损
（scripts/probe_onnx_smoke.py 实测最大差 0.144、top5 全重合）。
int8 量化两版均失真（差 8.6 / 相关性 0.19），弃用。

打分规则与 torch 版完全一致：
  分数 = logit(yes) − logit(no)，取最后 token 位置（左 padding 保证对齐真实 token）；
  阈值 NO_MATCH_TOP1_LOGIT 语义不变（复用 Reranker 常量，见 reranker.py）。
"""

import logging
import os
from pathlib import Path

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

from app import config
from app.rag.reranker import Reranker

logger = logging.getLogger(__name__)

# LogitScore 打分用的 yes/no token（Qwen3 词表固定 id）
_YES, _NO = "yes", "no"

# Instruct 头：模板默认值即此文本（角色 system 消息可覆盖；ST 也用同一句）
_INSTRUCT = "Given a web search query, retrieve relevant passages that answer the query"


def _register_cuda_dll_dirs() -> None:
    """让 ORT 能找到 pip 安装的 NVIDIA 运行库（site-packages/nvidia/*/bin）。

    onnxruntime-gpu 的 wheel 不捆绑 CUDA 运行库（cublas/cudnn/cudart 由
    nvidia-*-cu12 pip 包提供）。ORT 加载 providers_cuda.dll 时按系统顺序
    解析其依赖 DLL —— 实测 add_dll_directory 对 ORT 无效（其 LoadLibrary
    不带 LOAD_LIBRARY_SEARCH_DEFAULT_DIRS），必须加 PATH 才可见。
    """

    def _register(base: Path) -> None:
        for bin_dir in base.glob("nvidia/*/bin"):
            os.add_dll_directory(str(bin_dir))  # 双保险：新式 API 也注册
            os.environ["PATH"] = (
                str(bin_dir) + os.pathsep + os.environ["PATH"]
            )

    _register(Path(ort.__file__).resolve().parents[1])  # site-packages


class OnnxReranker:
    """ONNX Runtime 版精排：懒加载 ORT session，接口与 Reranker.rerank 一致。"""

    def __init__(self, onnx_dir: str | Path):
        onnx_dir = Path(onnx_dir)
        # tokenizer 从原始仓库加载（词表一致）：onnx 仓库自带 reranker 专用模板，期望
        # user content 是 dict，会破坏字符串渲染；原始仓库是标准 Qwen3 模板，
        # 与 ST(torch) 路径同源 → 输入逐位一致（块 3 验证）
        self.tokenizer = AutoTokenizer.from_pretrained(str(config.RERANKER_MODEL_PATH))
        # 与 ST 一致：左 padding（pad token = <|endoftext|>），保证最后 token 是真实 token
        self.tokenizer.padding_side = "left"
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        # CUDA 优先、CPU 兜底：ORT 自动按序选可用 provider（GPU 不可用时静默回退 CPU）
        _register_cuda_dll_dirs()  # 让 ORT 能找到 pip 装的 CUDA 运行库（仅 Windows 生效）
        self.session = ort.InferenceSession(
            str(onnx_dir / "model.onnx"),
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        self._yes_id = self.tokenizer.convert_tokens_to_ids(_YES)
        self._no_id = self.tokenizer.convert_tokens_to_ids(_NO)
        logger.info(f"ONNX 精排已加载：{onnx_dir}")

    # ---------- 输入生成（复刻 ST 的 chat template 渲染） ----------

    def _build_texts(self, pairs: list[tuple[str, str]]) -> list[str]:
        """(query, doc) 对 → chat template 渲染文本（与 ST preprocess 同源）。

        仓库的 chat_template.jinja 是 reranker 专用模板：从 role=system（Instruct）、
        role=query、role=document 三条消息取内容，system 段为模板硬编码。
        ST 的 parse_inputs 正是这样构造的 —— 必须同构，字符串 user 消息会渲染为空。
        """
        texts = []
        for query, doc in pairs:
            messages = [
                {"role": "system", "content": _INSTRUCT},
                {"role": "query", "content": query},
                {"role": "document", "content": doc},
            ]
            texts.append(
                self.tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=False
                )
            )
        return texts

    def _encode(self, texts: list[str]) -> dict[str, np.ndarray]:
        """批量编码 → ORT 输入（input_ids / attention_mask / position_ids，int64）。"""
        enc = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=1024,  # 与 torch 版 max_length 一致（见 reranker.py 注释）
            return_tensors="np",
        )
        ids = enc["input_ids"].astype(np.int64)
        attn = enc["attention_mask"].astype(np.int64)
        seq = ids.shape[1]
        pos = np.tile(np.arange(seq), (len(texts), 1)).astype(np.int64)
        return {"input_ids": ids, "attention_mask": attn, "position_ids": pos}

    def _scores(self, texts: list[str]) -> list[float]:
        """ORT 推理 → 手算 log-odds（与 LogitScore 同规则）。"""
        out = self.session.run(None, self._encode(texts))[0]
        return (out[:, -1, self._yes_id] - out[:, -1, self._no_id]).astype(float)

    # ---------- 对外接口（与 Reranker.rerank 一致） ----------

    def rerank(self, query: str, docs: list[dict], top_k: int = 5) -> list[dict]:
        """输入 docs: [{id, content, metadata}]，返回精排后 top_k 个（含 score）。"""
        if not docs:
            return []
        pairs = [
            (query, d["content"][: Reranker.RERANK_TRUNCATE]) for d in docs
        ]
        scores = self._scores(self._build_texts(pairs))
        scored = sorted(
            zip(docs, scores), key=lambda ds: float(ds[1]), reverse=True
        )
        # 整体无相关（库外问题）→ 空 → 图上走 generate_no_info 防幻觉
        if float(scored[0][1]) <= Reranker.NO_MATCH_TOP1_LOGIT:
            return []
        return [
            {**d, "score": float(s)} for d, s in scored[:top_k]
        ]
