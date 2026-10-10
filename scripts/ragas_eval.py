# 先加载环境变量再import其他模块，防止环境变量未加载，而模块内代码又依赖环境变量导致找不到
import os
import uuid

from dotenv import load_dotenv
load_dotenv()
from langchain_core.messages import HumanMessage
from openai import AsyncOpenAI
from ragas.embeddings.base import embedding_factory
from ragas.llms import llm_factory

import pandas as pd
from ragas import Dataset
from ragas.backends import InMemoryBackend
"""RAGAS 评估：读题 → 系统答题 → 存答题卡 eval_out/raw.json"""
import asyncio
import json
import sys
from pathlib import Path

#让scripts/ 里的 py 文件 能够 import 到项目根目录下的 app 模块（如 app/config.py）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.rag.agent import rag_agent


from ragas.metrics.collections import (
    Faithfulness,  # 忠实度
    AnswerRelevancy,  # 回答相关性
    ContextPrecision,  # 上下文精度
    ContextEntityRecall,  # 上下文实体召回
    NoiseSensitivity,  # 噪声敏感度
    ContextRecall  # 上下文召回
)

from ragas import experiment
from pydantic import BaseModel


OUT_PATH = Path(__file__).resolve().parents[1] / "eval_out" / "raw.json"

async def answer_one(questions: dict) ->dict:
    inputs = {"messages":[HumanMessage(content=questions["user_input"])]}
    config_runtime = {"configurable":{"thread_id": f"eval_{uuid.uuid4().hex[:8]}"}}
    result = await rag_agent.graph.ainvoke(input=inputs, config=config_runtime)

    return{
        "user_input": questions["user_input"],
        "reference": questions["reference"],
        "doc": questions["doc"],
        "type": questions["type"],
        "intent": result.get("intent"),
        "status": result.get("status"),
        "response": result.get("answer"),
        "retrieved_contexts":[r["content"] for r in (result.get("references") or [])]
    }


#定义评估用的LLM
llm_client = AsyncOpenAI(
    api_key = os.getenv("DEEPSEEK_API_KEY"),
    base_url = os.getenv("DEEPSEEK_BASE_URL")
)
evaluator_llm = llm_factory(model = "deepseek-chat", client = llm_client, max_tokens = 4096)

#定义评估用的Embedding模型
embedding_client = AsyncOpenAI(
    api_key = os.getenv("DASHSCOPE_API_KEY"),
    base_url= os.getenv("DASHSCOPE_BASE_URL")
)
evaluator_embedding = embedding_factory(model = "text-embedding-v3", client = embedding_client)

# 定义评估用的指标
class EvalResult(BaseModel):
    user_input: str
    context_recall: float = float("nan")
    context_precision: float = float("nan")
    faithfulness: float = float("nan")
    answer_relevancy: float = float("nan")

@experiment(experiment_model=EvalResult)
async def run_rag_eval(row):
    user_input = row.get("user_input")
    reference = row.get("reference")
    retrieved_contexts = row.get("retrieved_contexts")
    response = row.get("response")

    print(f"开始评估： {user_input[:15]}... , 进度 0/4")
    cr = cp = faith = ar = float("nan")

    # ---- 检索阶段指标（需要检索材料）----
    if retrieved_contexts:
        cr = (await ContextRecall(llm=evaluator_llm).ascore(
            user_input=user_input, reference=reference,
            retrieved_contexts=retrieved_contexts)).value
        print("完成 context_recall 评估 , 进度 1/4")
        cp = (await ContextPrecision(llm=evaluator_llm).ascore(
            user_input=user_input, reference=reference,
            retrieved_contexts=retrieved_contexts)).value
        print("完成 context_precision 评估 , 进度 2/4")
        faith = (await Faithfulness(llm=evaluator_llm).ascore(
            user_input=user_input, response=response,
            retrieved_contexts=retrieved_contexts)).value
        print("完成 faithfulness 评估 , 进度 3/4")

    # ---- 生成阶段指标：AnswerRelevancy 不需要上下文，chitchat 行也有分 ----
    ar = (await AnswerRelevancy(llm=evaluator_llm, embeddings=evaluator_embedding).ascore(
        user_input=user_input, response=response)).value
    print("完成 answer_relevancy 评估 , 进度 4/4")

    return EvalResult(user_input=user_input, context_recall=cr,
                      context_precision=cp, faithfulness=faith,
                      answer_relevancy=ar)

async def main() -> None:
    await rag_agent.init()
    print("RAG初始化完成")
    questions = json.load(
        open("eval/questions.json",encoding="utf-8")
    )

    # 遍历所有问题
    if OUT_PATH.exists():
        results = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        print(f"复用缓存 raw.json: {len(results)} 条")
    else:
        results = []
        for q in questions:
            rec = await answer_one(q)
            results.append(rec)
            print(f"{len(results)}/{len(questions)} {q['user_input'][:20]} -> {rec['status']}")

        # 保存结果，写入（eval_out/raw.json）
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    # 评分标准的唯一事实源 = eval/questions.json：
    # raw.json 只是答题卡（答案+上下文），其 reference 快照可能过期——
    # 改了评分标准（校准 reference）无需删答题卡重答，按 user_input 重新 join
    q_by_input = {q["user_input"]: q for q in questions}
    for rec in results:
        q = q_by_input.get(rec["user_input"])
        if q:
            rec["reference"] = q["reference"]
    print("评委准备完毕:", evaluator_llm, evaluator_embedding)

    df = pd.DataFrame(results)[["user_input", "reference", "response", "retrieved_contexts"]]
    dataset = Dataset.from_pandas(
        df, name="rag_kb_eval", backend=InMemoryBackend()
    )
    print("数据集:", len(dataset), "条 | 字段:", dataset.to_pandas().columns.tolist())
    # print(f'一共读到{len(questions)} 题')
    # print(questions[0]["user_input"]) #打印第一题，验证读到了文件内容
    # 测试单题的案例
    # rec = await answer_one(questions[0])
    # print(rec["status"], rec["intent"], len(rec["retrieved_contexts"]))

    #烟测：1 题 1 指标，验证评委链路，先别跑全量
    metric = Faithfulness(llm = evaluator_llm)
    row = dataset[0] # dataset[0] 就是一行 dict（4 个字段）
    result = await metric.ascore(
        user_input= row["user_input"],
        response= row["response"],
        retrieved_contexts= row["retrieved_contexts"]
    )
    print(f"Faithfulness(第1题): {result.value:.3f}")

    #批量评估：全量 15 题（不切分）
    tds = Dataset.from_pandas(
        dataset.to_pandas(),
        name="rag_eval",
        backend = "local/csv",
        root_dir = "./experiments"
    )
    results = await run_rag_eval.arun(tds, name="rag_with_chunk_v1")

    # 查看结果
    report = results.to_pandas()
    print("=" * 60)
    print("RAGAS 评估报告（均值）")
    print("=" * 60)
    print(report[["context_recall", "context_precision",
                  "faithfulness", "answer_relevancy"]].mean())

    # 两份中间产物：RAGAS 实验记录（./experiments/）+ 我们自己的答卷（eval_out/metrics.json）
    report.to_json(Path(__file__).resolve().parents[1] / "eval_out" / "metrics.json",
                   orient="records", force_ascii=False, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
