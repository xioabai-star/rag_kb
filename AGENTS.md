# AGENTS.md —— RAG 企业知识库问答系统

## 项目定位
RAG 企业知识库问答系统（后端 + Agent 方向）。
核心价值：LangGraph 显式 StateGraph 编排 + 混合检索（稠密+稀疏+RRF+本地精排）+ 多轮对话持久化 + SSE 流式 + 工具调用（报销数据 / 高德 MCP 地图）。

## 技术栈
Python + FastAPI + LangChain + **LangGraph** + DeepSeek（生成）+ DashScope embedding + Chroma（稠密）+ bm25s/jieba（稀疏）+ Qwen3-Reranker-0.6B（精排，本地 CPU）+ 高德 MCP（地图外部工具）+ Vue 3 + Vite（前端）

## 常用命令（项目根目录 D:\code\rag_kb）
```bash
# 安装依赖
uv sync

# 启动后端（:8002）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload

# 生成演示文档（sample_docs/）
uv run python scripts/generate_sample_docs.py

# 前端开发（:5173，proxy 到 8002）
cd frontend && npm install && npm run dev

# 前端生产构建（输出到 app/static/dist，FastAPI 自动挂载）
cd frontend && npm run build

# 端到端验证（需先启动后端）
uv run python scripts/upload_demo_docs.py  # 上传 sample_docs/ 并轮询入库状态
uv run python scripts/test_chat.py         # 6 用例 SSE 聊天 E2E：问答/多轮/闲聊/库外/历史/删除

# 报销演示数据（需先注册 admin 账号；幂等可重跑）
uv run python scripts/seed_demo_business_data.py
# 工具链路探针：报销/天气/路线/闲聊 4 用例 E2E（直连图；需 .env 配好 AMAP_KEY）
uv run python scripts/probe_tool_e2e.py
```

## 关键架构决策（详见 README「核心设计」）
- **LangGraph 图**：route_intent（结构化输出）→ 闲聊/改写分流 → retrieve → generate/generate_no_info 兜底（防幻觉第一道防线）
- **混合检索**：Chroma top10 + BM25 top10 → RRF(k=60) 融合 → top15 → CrossEncoder 精排 → top5
- **双索引一致性**：唯一事实源 = Chroma；增删文档后 BM25 全量重建（document_manager 强制）
- **多轮会话**：AsyncSqliteSaver checkpointer（消息状态）+ sessions 业务表（列表展示）双存储，删除级联双删
- **上下文管理 v1**：滑动窗口剪枝 [-HISTORY_WINDOW*2:]；总结压缩（RemoveMessage）为 v1.1 规划
- **SSE 流式**：stream_mode=["messages","updates"]，事件协议 {event, data}：session/status/message/references/done/error
- **同步库不阻塞事件循环**：检索/精排全部 asyncio.to_thread（硬性规则）
- **异步入库**：上传即返回 processing，后台任务处理，失败回滚（Chroma 删块），侧车 documents.json 记录状态
- **工具双通道 + MCP**：意图路由 tool_name 三选一（报销/路线/天气）→ tool_node dispatcher 分流 → generate_tool 按通道选话术；报销=数据通道（直连业务库按 user_id 过滤），天气/路线=外部通道（高德 MCP 协议接入：streamable_http_client + ClientSession）；结构化输出选择题 > function calling（实测强填参数坑：报销被填成 route）；LLM 只做选择与抽参，调度写死在代码（可控可测）；agent.py 零改动（status 透传、节点拓扑不变）
- **多轮工具追问**：工具分支分类/抽参注入最近历史（地名继承，偏好词不抽）；知识库用改写节点（下游需要独立问句）——同问题两解法

## 目录速览
```
app/config.py                全局配置（路径全部 BASE_DIR 派生，环境变量可覆盖）
app/rag/graph.py             ★ LangGraph 图（核心）
app/rag/agent.py             RAGAgent：checkpointer 生命周期 + SSE 生成器
app/rag/hybrid_retriever.py  混合检索管线（依赖注入）
app/rag/document_manager.py  入库编排：ingest/delete/list + 回滚
app/rag/{embeddings,vectorstore,bm25_index,fusion,reranker,loader_registry,chunking}.py
app/api/{documents,chat,sessions,auth}.py  REST + SSE + JWT 登录
app/models/{session,user,reimbursement}.py  业务表（报销单 = 工具数据源）
app/tools/amap_tools.py      高德 MCP 客户端（懒加载会话/断线自愈/aclose/route_plan 显式链）
frontend/                    Vue 3 + Vite（3 组件 + 1 SSE composable）
scripts/generate_sample_docs.py  生成 5 份演示文档
scripts/upload_demo_docs.py      上传演示文档 + 轮询状态（UTF-8 文件名可靠）
scripts/test_chat.py             6 用例 SSE 聊天端到端测试
scripts/repro_rerank.py          精排截断性能复现实验
sample_docs/                 演示文档（随仓库提交）
```

## 环境事实
- Windows；bash 是 Git Bash；路径用 /d/code/rag_kb
- Python 由 uv 管理（.venv），pypi 清华镜像
- 密钥在 .env（gitignore，绝不提交）；精排模型 D:/code/ai_demo/models/Qwen3-Reranker-0.6B（不入库，config 默认路径）
- PDF 解析默认 mineru 模式（云端 OCR），可设 PDF_MODE=pypdf 快速模式

## 会话延续
Codex 绑定启动目录：在 D:\code\rag_kb 下 `Codex --continue`（或 -c）延续上一会话；
跨目录用 `Codex --resume`（或 -r）按会话列表选择。本文件帮助新会话快速恢复上下文。
