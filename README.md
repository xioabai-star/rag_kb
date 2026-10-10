# RAG 企业知识库问答系统

基于 **LangGraph 显式 StateGraph 编排**的企业知识库智能问答系统：上传企业文档（PDF / Word / Markdown），即可用自然语言查询制度、流程、产品信息。支持多轮对话、引用溯源、流式输出（SSE）。

---

## 一、系统简介

| 能力 | 说明 |
|------|------|
| 文档入库 | 上传 md/txt/pdf/docx，后台异步解析（MinerU 支持扫描件 OCR）、分块、向量化 |
| 混合检索 | 稠密（Chroma + DashScope embedding）+ 稀疏（BM25 + jieba 中文分词）→ RRF 融合 → 本地精排（Qwen3-Reranker-0.6B） |
| Agent 编排 | LangGraph 显式图：意图路由 → 查询改写/工具调度 → 检索或工具 → 生成/兜底 |
| 工具调用 | 报销业务数据查询 + 高德 MCP 天气/路线，按 user_id 隔离业务数据 |
| 多轮对话 | AsyncSqliteSaver checkpointer 持久化会话，滑动窗口剪枝上下文 |
| 引用溯源 | 回答标注 [n]，可展开查看来源文档、章节与原文片段 |
| 流式输出 | SSE 协议打字机效果，前端 Vue 3 + Vite |

## 二、架构图

```
┌───────────────────────────── Web 前端 (Vue 3 + Vite) ─────────────────────────────┐
│  DocumentList(上传/列表/删除)   SessionList(会话)   ChatArea(流式问答/引用溯源)     │
└───────────────┬────────────────────────────────────────────────┬──────────────────┘
                │ REST (/api/v1/documents,sessions)              │ POST /chat/stream (SSE)
┌───────────────▼────────────────────────────────────────────────▼──────────────────┐
│                              FastAPI 服务层 (app/api)                              │
└───────┬────────────────────────────────────┬──────────────────────────────────────┘
        │                                    │
┌───────▼───────────┐              ┌─────────▼────────────┐
│  DocumentManager  │              │   RAGAgent (agent)   │
│  入库编排/回滚     │              │  checkpointer 生命周期│
└───────┬───────────┘              └─────────┬────────────┘
        │                                    │
┌───────▼────────────────────────────────────▼────────────┐
│           LangGraph 图（app/rag/graph.py）                 │
│  START → route_intent ──→ chitchat_node → END             │
│             ├→ rewrite_question → retrieve                │
│             │                    ├→ generate → END        │
│             │                    └→ generate_no_info → END│
│             └→ tool_node → generate_tool → END            │
└───────┬──────────────────────────────┬───────────────────┘
        │                              │
┌───────▼──────────────┐   ┌───────────▼─────────────┐
│  混合检索管线          │   │  双存储                  │
│  Chroma(稠密)          │   │  checkpointer(消息状态)  │
│  BM25(稀疏)            │   │  sessions 表(业务元数据) │
│  RRF 融合 → 精排       │   │  documents.json(侧车)   │
└──────────────────────┘   └─────────────────────────┘
```

## 三、技术栈

- **语言/框架**：Python 3.12 · FastAPI · Uvicorn
- **Agent 编排**：LangGraph（显式 StateGraph，非 create_agent）
- **LLM 组件**：LangChain · DeepSeek（deepseek-chat 生成）· DashScope text-embedding-v3（向量化）
- **检索**：Chroma（稠密）· bm25s + jieba（稀疏）· Qwen3-Reranker-0.6B（本地精排，CPU）
- **文档解析**：MinerU（复杂 PDF/扫描件/Word，云端 OCR）· PyPDFLoader（快速模式）
- **持久化**：AsyncSqliteSaver（会话状态）· aiosqlite（业务表）· 侧车 JSON（文档登记）
- **前端**：Vue 3 + Vite（3 组件 + 1 SSE composable）

## 四、核心设计

### 1. 显式 StateGraph 编排
RAG 流水线有**明确、固定的执行顺序**（检索 → 生成），且需要精确控制分支（意图路由、无结果兜底）。显式图让节点、边和条件分支都可以独立调试与测试。工具调用也由确定的节点调度，避免给固定流程增加不必要的循环和延迟。

### 2. 混合检索与 RRF 融合
- 稠密（Chroma 语义向量）理解"意思相近"，但**不擅长精确匹配**：专有名词、编号（"HR-2024-007"）、缩写（"五险一金"）容易漏召回；
- 稀疏（BM25 关键词）精确匹配好，但**不懂语义**："如何请假" 匹配不到"休假申请"。
两者互补，融合后召回更全。
- **RRF 不需要训练**：只依赖两个列表的排名，公式 `score(d) = Σ 1/(k + rank)`（k=60）。加权平均需要归一化分数，但稠密（余弦相似度）和稀疏（BM25 分数）的**分布形态完全不同**（一个是 [0,1]，一个无上限），归一化会扭曲信息且权重需要调参/训练；RRF 只比排名，天然鲁棒、零训练。

### 3. 本地精排
向量检索是"粗筛"（一阶段），计算快但精度有限。CrossEncoder 把 (query, 文档) **拼接输入模型**做精确打分，精度显著高于双塔式的向量检索，是 RAG 提升答案质量最有效的手段之一。只对 RRF 融合后的 15 个候选精排，计算量可控（CPU 约 1~3 秒）。本地部署还有数据合规优势：**文档内容不离开内网**。

### 4. 意图路由
大量企业知识库请求是闲聊（"你好"、"谢谢"）、知识库问题或工具请求。统一走检索会：浪费 token（检索内容塞进 prompt）、降低响应质量（不相关内容干扰）、**放大幻觉风险**（模型可能硬答）。route_intent 用结构化输出将请求分为 `kb_question`、`chitchat`、`tool_request` 三类：闲聊直接回答，知识库问题进入检索，报销/天气/路线请求进入工具 dispatcher。

### 5. 多轮对话与上下文管理
- 持久化：AsyncSqliteSaver checkpointer 按 `thread_id` 存取消息状态，图运行时自动续接历史；
- 上下文管理 v1：**滑动窗口剪枝**，只把最近 N 轮（N=6）消息送入 LLM，`messages[-N*2:]`；
- v1.1 规划：**总结压缩**——独立 `summary` 字段 + 增量更新（新轮次结束时把新内容追加进 summary），超窗时用 `RemoveMessage` 移除旧消息并把 summary 作为首条消息注入。剪枝简单但丢失早期关键信息；总结保信息但多一次 LLM 调用，先剪枝后总结是渐进式迭代。

### 6. 引用溯源
生成节点的 prompt 中把每个检索块编号为 `[n] 来源:{filename} {header}\n{content}`，要求模型回答中标注 [n]；节点的返回值带上 `references` 表（编号、chunk_id、文件名、章节标题、原文片段），随 SSE 事件推给前端展示，便于核对答案来源。

### 7. 无依据回答的处理
1. **检索为空 → 走 generate_no_info 兜底分支**，固定话术"知识库暂无相关内容"，绝不硬答（第一道防线，图结构保证）；
2. 系统提示词约束"只依据参考资料回答，禁止编造"；
3. 温度设 0.3（生成偏保守）；
4. 引用溯源让用户能核对来源（事后可查）；
5. RAGAS 评估：`eval/questions.json` 提供 50 题评估集，`scripts/ragas_eval.py` 记录 Context Recall / Context Precision / Faithfulness / Answer Relevancy，用于回归验证。

### 8. 双索引一致性
**唯一事实源 = Chroma collection**。BM25 索引不自建语料，每次增删文档后由 DocumentManager 从 Chroma **全量重建**（全量重建对万级块以下规模成本可接受，换来逻辑简单、绝无脏索引）。重建期间检索读旧索引（先 build 后替换引用，Python 赋值原子），不中断服务。

### 9. 异步入库与失败回滚
MinerU 解析 PDF 可能分钟级，同步处理会阻塞 HTTP。设计：`ingest()` 落盘 + 登记 `processing` 状态 + `asyncio.create_task` 后台处理，**立即返回**；后台任务执行 加载→分块→写 Chroma→重建 BM25→标记 `ready`。任何一步失败 → `_fail_and_rollback`：删除已写入的向量块（不留半成品），标记 `failed` 并记录错误信息。侧车 `documents.json` 是文档登记表，前端轮询状态徽章（processing/ready/failed）。

### 10. SSE 流式响应
- 后端 `EventSourceResponse`：以 `stream_mode=["messages","updates"]` 驱动图执行，`messages` 流吐 token（打字机），`updates` 流在节点完成时推状态（意图/引用）；
- 前端 fetch + `ReadableStream` 手动拆帧：按 `\n\n` 分隔符解析 `event:`/`data:` 行，**半帧缓冲**处理网络分片（一帧被拆到两次 read）；
- 选 SSE 的理由：单向推送足够（用户→服务端走普通 POST），协议简单、自动重连、**标准 HTTP 兼容代理/负载均衡**；WebSocket 双向能力用不上，复杂度反而高。

## 五、快速开始

```bash
# 1. 安装后端依赖
uv sync

# 2. 配置密钥：复制 .env.example 为 .env 并填入
#    DEEPSEEK_API_KEY（DeepSeek）、DASHSCOPE_API_KEY（阿里云百炼）、MINERU_TOKEN（可选，PDF 解析）

# 3. 生成演示文档（sample_docs/）
uv run python scripts/generate_sample_docs.py

# 4. 启动后端（:8002）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload

# 5. 启动前端（:5173，另开终端）
cd frontend && npm install && npm run dev
```

打开 http://127.0.0.1:5173 → 上传 sample_docs/ 下的文档 → 提问。

演示数据说明：为避免使用真实企业的敏感制度和个人信息，项目使用自构造的“云帆科技”演示文档，模拟考勤、请假、差旅、报销和信息安全等企业制度。数据内容是虚构的，但文档之间的引用关系、边界问题和评估场景按企业知识库使用方式设计。

生产模式：`cd frontend && npm run build` 后 FastAPI 直接托管 http://127.0.0.1:8002（前端构建产物输出到 `app/static/dist`）。当前项目定位为本地可运行演示版，Docker/Nginx 公网部署仍是后续工作。

## 六、功能体验

1. **上传 3 类文档**：员工手册.md / 差旅报销制度.pdf / 产品操作指南.docx → 状态转 ready
2. **知识库问答**："报销时限是多久？" → 命中差旅制度，带 [1] 引用 → 展开看来源
3. **多轮对话**：追问"那发票丢了怎么办？" → 查询改写生效（指代消解）
4. **闲聊分流**："你好" → 走 chitchat，不检索
5. **防幻觉**："公司今年的年度目标是什么？" → 知识库无此内容 → no_info 兜底
6. **会话管理**：新建会话 → 历史隔离；删除会话 → 双存储级联清除
7. **换主题检索**："年假标准" → 命中员工手册

## 七、后续规划（迭代路线）

- [x] RAGAS 评估（50 题）量化检索与生成质量
- [x] JWT 用户认证与会话/业务数据隔离
- [x] 报销查询工具与高德 MCP 天气/路线工具
- [ ] 上下文总结压缩（summary 字段 + RemoveMessage，替换 v1 剪枝）
- [ ] 文档更新（同 doc_id 覆盖重传，增量更新向量与索引）
- [ ] 会话流式保存（回答完整入库，刷新页面历史完整可读）
- [ ] 部署：Docker 化 + Nginx（静态 + 反向代理）

## 八、API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/documents/upload | 上传文档（multipart），立即返回 |
| GET | /api/v1/documents | 文档列表（含状态/块数） |
| DELETE | /api/v1/documents/{doc_id} | 删除文档（级联清索引） |
| POST | /api/v1/chat/stream | 流式问答（SSE） |
| GET | /api/v1/sessions | 会话列表 |
| GET | /api/v1/sessions/{thread_id}/messages | 会话历史 |
| DELETE | /api/v1/sessions/{thread_id} | 删除会话（双存储级联） |
| GET | /api/v1/health | 健康检查 |

## 九、安全与工程实践

- `.env` 密钥绝不入库（.gitignore 排除），`uv.lock` 锁定依赖版本；
- 检索与入库全部 `asyncio.to_thread`，事件循环零阻塞；
- 上传文件名消毒（`Path(filename).name` 防路径穿越）；
- 文档解析失败自动回滚，不留脏数据；
- 原子写入（BM25 索引临时文件 + rename），崩溃不损坏。
