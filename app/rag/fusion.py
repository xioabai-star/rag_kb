"""模块 5：检索结果融合算法（移植自 RAG notebook，已验证）。

- reciprocal_rank_fusion：RRF 融合——只依赖排名（1/(k+rank) 累加），
  零训练、零调参、对分数尺度差异鲁棒（优于加权平均，详见 README 设计决策）
- min_max_normalize：辅助函数（归一化到 0~1，保留作对照/备用）
"""


def reciprocal_rank_fusion(
    ranked_lists: list[list[dict]], k: int = 60
) -> list[dict]:
    """把多份排序列表融合为一份降序列表。

    输入示例：[稠密结果 [{id, content}, ...], 稀疏结果 [{id, content}, ...]]
    每个元素：第 j 份列表中的第 i 名贡献分数 1/(k + i + 1)
    输出：按总分降序去重后的列表
    """
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}

    for ranked_list in ranked_lists:
        for rank, item in enumerate(ranked_list):
            item_id = item["id"]
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
            items[item_id] = item

    # 按总分降序
    ranked = sorted(items.values(), key=lambda it: scores[it["id"]], reverse=True)
    return ranked


def min_max_normalize(scores: list[float]) -> list[float]:
    """线性归一化到 [0, 1]（min-max）。"""
    if not scores:
        return []
    low, high = min(scores), max(scores)
    if high == low:
        return [0.0] * len(scores)
    return [(s - low) / (high - low) for s in scores]
