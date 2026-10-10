# 单题回归探针：意图路由修复验证（只看 intent/status，不落盘）
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv; load_dotenv()
from langchain_core.messages import HumanMessage
from app.rag.agent import rag_agent

async def main() -> None:
    await rag_agent.init()
    questions = json.load(open("eval/questions.json", encoding="utf-8"))

    targets = [
        questions[8],   # 翻车点：8点半上班（必须翻成 kb_question）
        questions[0],   # 对照：试用期工资（必须保持 kb_question）
        questions[6],   # 对照：发票丢失（必须保持 kb_question）
        {"user_input": "今天天气不错啊，出来聊聊天吗？"},  # 对照组：真闲聊（必须保持 chitchat）
    ]
    for q in targets:
        result = await rag_agent.graph.ainvoke(
            input={"messages": [HumanMessage(content=q["user_input"])]},
            config={"configurable": {"thread_id": f"route_check_{q['user_input'][:5]}"}},
        )
        print(f"intent={result.get('intent'):<12s} status={result.get('status'):<10s} "
            f"{q['user_input'][:20]}")

asyncio.run(main())
