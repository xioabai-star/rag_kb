"""验证辅助：测量 CrossEncoder 输入长度对 CPU 前向耗时的影响。

背景：reranker.py:21-25 注释称"不传 max_length 会 padding 到 4 万 token（17s/对），
传 1024 后秒级"。首轮复现实测：长输入（~1200 字）两组均为 ~16-17s——
说明瓶颈是 CPU 前向本身而非 padding，max_length 只把 1200 截到 1024（省 15%）。
本脚本第三组验证生产同款截断（RERANK_TRUNCATE=200 字）是否才是量级优化。

用法：uv run python scripts/repro_rerank.py
"""

import sys
import time
from pathlib import Path

# 先加载 .env（与 main.py 同款顺序：env 先于业务 import——config 读 os.environ）
from dotenv import load_dotenv

load_dotenv()

# 让脚本能 import 项目模块（scripts/ 下运行时，项目根不在 sys.path 里）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from sentence_transformers import CrossEncoder  # noqa: E402

QUERY = "出差报销需要什么材料？"
# 长文本（~1200 字，远超生产 chunk 的 500 字上限，用于对比截断效果）
LONG_DOC = "员工须在费用发生后 30 天内通过 OA 系统提交报销申请，附发票原件及行程单，财务部审核后付款。" * 30
# 生产同款：reranker.py:30 RERANK_TRUNCATE = 200（predict 前截到 200 字）
SHORT_DOC = LONG_DOC[:200]


def time_predict(model, text: str, label: str) -> float:
    """对 (QUERY, text) 做一次 predict，打印耗时，返回秒数。"""
    t0 = time.time()
    model.predict([(QUERY, text)], show_progress_bar=False)
    elapsed = time.time() - t0
    print(f"  {label}: {elapsed:.1f}s")
    return elapsed


def main() -> None:
    model_path = config.RERANKER_MODEL_PATH
    print(f"模型路径: {model_path}")

    # [1/3] 不带 max_length（继承模型 config 的 40960）
    print("[1/3] 加载不带 max_length 的 CrossEncoder ...")
    m_slow = CrossEncoder(model_path, device="cpu")
    t1 = time_predict(m_slow, LONG_DOC, "不带 max_length, 长输入 1200 字")

    # [2/3] 与 [3/3] 共用同一实例（max_length 是构造参数，实例只需加载两次）
    print("[2/3] 加载 max_length=1024 的 CrossEncoder ...")
    m_fast = CrossEncoder(model_path, device="cpu", max_length=1024)
    t2 = time_predict(m_fast, LONG_DOC, "max_length=1024, 长输入 1200 字")
    t3 = time_predict(m_fast, SHORT_DOC, "max_length=1024, 截到 200 字（生产同款）")

    print(f"\n===== 观测结果 =====")
    print(f"[1/3] 不带 max_length 长输入 : {t1:.1f}s")
    print(f"[2/3] max_length=1024 长输入 : {t2:.1f}s")
    print(f"[3/3] max_length=1024 截 200 : {t3:.1f}s")
    print()
    print("对照 reranker.py 注释的说法：")
    print(f"  注释称不传 max_length 会 pad 到 4 万 token（17s/对）——若真如此，[1/3] 应远慢于 [2/3]。")
    print(f"  实测 [1] vs [2] 差 {abs(t1-t2):.1f}s → "
          f"{'机制未复现（版本差异？），瓶颈是 CPU 前向本身' if abs(t1-t2) < 5 else '机制复现，max_length 显著生效'}")
    print(f"  生产同款截 200 字 vs 长输入，差 {abs(t2-t3):.1f}s → "
          f"{'截断 200 字是量级优化（生产优化有效）' if t3 < t2 * 0.5 else '截断收益有限'}")


if __name__ == "__main__":
    main()
