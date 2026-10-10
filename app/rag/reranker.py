"""模块 6：精排（CrossEncoder，本地 Qwen3-Reranker-0.6B）。

两阶段检索的第二阶段：对 RRF 融合后的少量候选（15 个），
把 (query, 文档) 拼接送入模型精确打分，只保留最相关的 top_k。
本地部署：文档内容不离开内网（数据合规）；CPU 可跑（15 对约 1~3 秒）。
"""

import logging
import threading
from pathlib import Path

from sentence_transformers import CrossEncoder

from app import config

logger = logging.getLogger(__name__)


class Reranker:
    """对候选文档重排：模型加载约十几秒，懒加载 + 单例复用。"""

    def __init__(self, model_path: str, device: str = "cpu"):
        # 显式传 max_length=1024（防御）：不传会继承 Qwen3 config 的 40960，部分
        # sentence-transformers 版本会把输入 padding 到 4 万 token（历史踩坑：17s/对）。
        # 本机当前版本未复现该行为（scripts/repro_rerank.py 实测：长输入 16.7s vs 16.0s），
        # 瓶颈在 CPU 前向本身；真正的量级优化是下面的 RERANK_TRUNCATE 截断（200 字实测 16s→3.3s，约 5 倍）
        self.model = CrossEncoder(model_path, device=device, max_length=1024)
        logger.info(f"精排模型已加载：{model_path} (device={device}, max_length=1024)")

    # 精排输入截断长度（字）：截断是性能手段，但窗口必须盖住答案正文。
    # 200 字曾把 CPU 前向从 16s 降到 3.3s（见 scripts/repro_rerank.py）；RAGAS 评估+块地图
    # 发现答案被埋在块内 200~370 字处（probe_chunk_map.py：考勤"迟到"@231 藏于大块深处），
    # 精排全盲 → 扩到 500 修好 CP；随后分块改为表格按行切（chunking.py），全库最大块
    # 仅 237 字且答案位于块首 → 把窗口回收回 300（预期 CP 零回归 + 延迟过半程，留待评估验证）
    RERANK_TRUNCATE = 300
    # 整体无相关的判定线（logit）：top1 低于此线视为知识库无相关内容，
    # 返回 [] 触发 generate_no_info 兜底。实测无关块（年假/五险一金/产品指南）
    # 在 -7 以下，弱相关（发票 vs 报销制度）在 -3~-5，取 -5 为界。
    NO_MATCH_TOP1_LOGIT = -5.0

    def rerank(self, query: str, docs: list[dict], top_k: int = 5) -> list[dict]:
        """输入 docs: [{id, content, metadata}]，返回精排后 top_k 个（含 score）。

        注意：predict 用截断文本打分，返回的 content 仍是完整原文。
        不做逐块阈值过滤（会误伤弱相关真实查询），只看 top1 是否整体无相关。
        """
        if not docs:
            return []
        pairs = [
            (query, d["content"][: self.RERANK_TRUNCATE]) for d in docs
        ]
        scores = self.model.predict(pairs, show_progress_bar=False)
        scored = sorted(
            zip(docs, scores), key=lambda ds: float(ds[1]), reverse=True
        )
        # 整体无相关（库外问题）→ 空 → 图上走 generate_no_info 防幻觉
        if float(scored[0][1]) <= self.NO_MATCH_TOP1_LOGIT:
            return []
        return [
            {**d, "score": float(s)} for d, s in scored[:top_k]
        ]


# ==================== 进程级单例 ====================

_lock = threading.Lock()
_instance: Reranker | None = None


def get_reranker():
    """进程级单例：按 RERANK_BACKEND 分发 onnx/torch 后端；onnx 不可用时自动回退 torch。

    锁防止并发请求同时触发首次加载（十几秒）重复加载。
    返回 OnnxReranker 或 Reranker（二者接口一致，调用方无感知）。
    """
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                if config.RERANK_BACKEND == "onnx":
                    onnx_dir = Path(config.ONNX_RERANKER_DIR)
                    if (onnx_dir / "model.onnx").exists():
                        from app.rag.reranker_onnx import OnnxReranker

                        _instance = OnnxReranker(onnx_dir)
                        return _instance
                    logger.warning(
                        f"ONNX 模型不存在（{onnx_dir}），回退 torch 精排"
                    )
                _instance = Reranker(config.RERANKER_MODEL_PATH)
    return _instance
