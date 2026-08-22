# FastAPI LLM News

面向新闻场景的 AI 后端综合学习项目，重点实践异步 API、JWT 鉴权、Redis 缓存、LangChain RAG、Chroma 向量检索、Qwen 和 SSE 流式响应，并加入基础的超时、并发、日志和健康检查治理。

本项目用于求职展示和工程学习，不宣称达到生产级。

## 系统架构

```mermaid
flowchart LR
    Vue[Vue 3] -->|HTTP / SSE| API[FastAPI]
    API --> Auth[JWT鉴权]
    API --> DB[(MySQL)]
    API --> Cache[(Redis)]
    API --> Rewrite[Query Rewrite]
    Rewrite --> Embed[DashScope Embedding]
    Embed --> Chroma[(Chroma)]
    Chroma --> Prompt[ChatPromptTemplate]
    Prompt --> Qwen[Qwen]
    Qwen -->|Token流| API
```

离线 RAG：

```text
MySQL新闻 → 文本组装 → RecursiveCharacterTextSplitter
→ DashScope Embedding → Chroma持久化
```

在线问答：

```text
用户问题 → JWT鉴权 → 会话与历史 → Query Rewrite
→ Embedding → Chroma Top-K + 阈值过滤 → Prompt
→ Qwen → SSE → 完整成功后保存assistant消息
```

## 技术栈

- 前端：Vue 3、Pinia、Axios、Fetch ReadableStream
- 后端：FastAPI、Pydantic Settings
- 数据库：MySQL、SQLAlchemy AsyncSession、aiomysql
- 缓存：Redis AsyncIO，Cache-Aside
- AI：LangChain LCEL、DashScope OpenAI 兼容接口、Qwen
- RAG：DashScope `text-embedding-v3`、Chroma
- 流式协议：SSE `text/event-stream`
- 测试：pytest、pytest-asyncio

## 核心目录

```text
FastAPI-LLM/
├─ backend/
│  ├─ main.py                 # FastAPI、trace_id、CORS、生命周期
│  ├─ config/                 # Settings、DB、Redis、Chroma
│  ├─ routers/                # 用户、新闻、AI、健康检查
│  ├─ crud/                   # AsyncSession数据库操作和缓存编排
│  ├─ models/                 # SQLAlchemy模型
│  ├─ services/               # 模型、Retriever、并发和摘要
│  ├─ services/RAG_chroma/    # 新闻离线向量化入库
│  ├─ evaluation/             # 最小RAG评测
│  ├─ tests/                  # 核心工程测试
│  └─ docs/                   # 项目审计与优化报告
├─ frontend/
├─ database.sql
└─ compose.yaml               # Redis
```

## 环境要求

- Windows 10/11
- Conda 环境：`D:\Anaconda\envs\fastapi311`
- Python 3.11
- MySQL 8.x
- Docker Desktop（用于 Redis，可选）
- Node.js 与 npm

## 环境变量

公开模板为 `backend/.env.example`。不要提交真实 `.env`、`.env.local`、API Key、JWT Secret 或数据库密码。

```powershell
cd C:\Users\31209\Desktop\FastAPI-LLM\backend
Copy-Item .env.example .env
```

至少配置：

```dotenv
DATABASE_URL=mysql+aiomysql://USER:PASSWORD@127.0.0.1:3306/news_app?charset=utf8mb4
DASHSCOPE_API_KEY=your-key
JWT_SECRET_KEY=your-long-random-secret
```

加载优先级为系统环境变量、`backend/.env.local`、`backend/.env`。`.env.local` 适合本机覆盖配置，已经被 Git 忽略。

主要工程参数：

| 变量 | 默认值 | 作用 |
| --- | ---: | --- |
| `CORS_ORIGINS` | 本机 5173 两个地址 | 允许访问API的前端来源，逗号分隔 |
| `LLM_REQUEST_TIMEOUT_SECONDS` | 60 | 普通模型调用和Query Rewrite超时 |
| `LLM_STREAM_TIMEOUT_SECONDS` | 120 | 一次SSE模型流的总超时 |
| `LLM_MAX_CONCURRENCY` | 4 | 单进程同时占用上游AI的请求数 |
| `APP_LOG_LEVEL` | INFO | 应用日志最低级别 |
| `RAG_DEBUG_LOG` | false | 是否输出截断后的chunk、RAG上下文和Prompt摘要 |
| `RAG_CHUNK_SIZE` | 500 | 新闻文本块最大字符数 |
| `RAG_CHUNK_OVERLAP` | 100 | 相邻文本块重叠字符数 |
| `RAG_TOP_K` | 5 | 初始检索文本块数量 |
| `RAG_SCORE_THRESHOLD` | 0.5 | cosine relevance score低于该值的块不进入Prompt |
| `CHROMA_COLLECTION_NAME` | news_rag_cosine_candidate | 当前在线使用的cosine集合 |

完整列表见 `backend/.env.example`。

## 数据库初始化

`database.sql` 是原始新闻业务数据库脚本，包含用户、旧 Token、新闻分类、新闻、相关新闻、收藏、历史和旧 `ai_chat` 表。

```powershell
mysql -u root -p < C:\Users\31209\Desktop\FastAPI-LLM\database.sql
```

当前 JWT 和新聊天链还使用以下 ORM 表：

- `user_auth_state`
- `user_refresh_token`
- `user_token_blacklist`
- `user_chat_session`
- `chat_message`

应用启动时，`services/db_bootstrap.py` 通过 SQLAlchemy `create_all` 确保这些表存在。当前没有 Alembic；`database.sql` 与新表由启动补齐的状态属于已知限制。此次优化没有改变 ORM 字段，也不需要执行 ALTER TABLE 升级脚本。

## Redis 启动

推荐从项目根目录启动：

```powershell
cd C:\Users\31209\Desktop\FastAPI-LLM
docker compose up -d redis
docker compose ps
```

Compose 包含 Redis healthcheck 和 `redis_data` 持久卷。

Redis 缓存：

- 新闻分类：默认 7200 秒
- 新闻列表：默认 1800 秒
- 新闻详情：默认 300 秒
- 相关新闻：默认 1800 秒

Redis 不可用时，缓存函数记录结构化警告并按缓存未命中处理，新闻查询回退到 MySQL。当前缓存主要依赖 TTL 过期，没有完善的主动失效广播。

## 后端启动

```powershell
cd C:\Users\31209\Desktop\FastAPI-LLM\backend
D:\Anaconda\envs\fastapi311\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

接口文档：

- Swagger UI：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

`GET /health` 只检查 Backend、MySQL 和 Redis，不调用 LLM。

## 前端启动

```powershell
cd C:\Users\31209\Desktop\FastAPI-LLM\frontend
npm install
npm run dev
```

默认前端地址为 `http://127.0.0.1:5173`，后端地址在 `frontend/src/config/api.js` 中配置。

## 新闻写入 Chroma

执行前确认 MySQL 新闻数据、DashScope Key 和 RAG 参数：

```powershell
cd C:\Users\31209\Desktop\FastAPI-LLM\backend
D:\Anaconda\envs\fastapi311\python.exe -m services.RAG_chroma.add_to_chroma
```

入库脚本对每篇新闻先按 `news_id` 删除旧块，再写入当前块，避免同一新闻重复累积。Embedding 模型和在线检索统一读取 `DASHSCOPE_EMBEDDING_MODEL`。

注意：已有 `chroma_db` 集合的真实距离度量不会因为修改环境变量自动改变。当前历史集合曾检查到 L2，而新建集合配置意图为 cosine；切换度量需要备份后重建集合并运行评测，不能直接修改持久化元数据。

## AI问答接口

```http
POST /api/ai/chat
Authorization: Bearer <access-token>
Content-Type: application/json
```

```json
{
  "messages": [{"role": "user", "content": "中国科技成就有哪些"}],
  "stream": true,
  "session_id": null
}
```

兼容性保持不变：

- 请求中的旧 `model` 字段仍可存在，但后端只使用 `LLM_MODEL_ID`。
- 新会话 ID 继续通过 `X-Session-Id` 返回。
- Token 增量仍使用 `choices[0].delta.content`。
- SSE 额外发送 `sources` 数据帧；旧前端会忽略没有 `delta.content` 的帧。
- 普通 JSON 响应在 `data` 中追加 `sources`。

## JWT与安全

- 密码使用 bcrypt 哈希，不保存明文。
- 登录签发短期 Access Token 和长期 Refresh Token。
- JWT 验证签名、`iss`、`aud`、`typ`、`nbf`、`exp`、`jti` 和 token version。
- Access Token 支持黑名单，Refresh Token 支持轮换和撤销。
- `JWT_SECRET_KEY` 只来自环境变量。
- 参数、数据库和未知异常使用统一安全响应；完整异常写服务端日志，不向客户端返回堆栈。
- 每个请求返回 `X-Trace-Id`，AI日志记录会话、RAG、LLM和总耗时。

## 超时、并发与SSE

- 单进程使用 `asyncio.Semaphore` 限制 AI 并发，不影响普通新闻接口。
- 等待并发槽位超时返回 503。
- Query Rewrite、Retriever、普通 LLM 和 SSE 分别有明确超时。
- SSE 每个模型块检查客户端断连，取消时不保存不完整 assistant 消息。
- assistant 消息只在模型流完整结束后写入。
- 流式保存使用短生命周期独立 AsyncSession，避免请求 Session 长期占用连接。

该 Semaphore 只约束单个 Uvicorn 进程；多 worker 部署需要 Redis 限流或统一网关，这是后续工作。

## 测试

```powershell
cd C:\Users\31209\Desktop\FastAPI-LLM\backend
D:\Anaconda\envs\fastapi311\python.exe -m pytest -q tests
D:\Anaconda\envs\fastapi311\python.exe -m compileall -q .
```

## RAG评测

默认只评估检索和 Recall@K，不调用聊天模型：

```powershell
D:\Anaconda\envs\fastapi311\python.exe -m evaluation.evaluate_rag
```

需要同时生成回答：

```powershell
D:\Anaconda\envs\fastapi311\python.exe -m evaluation.evaluate_rag --generate-answers
```

评测框架已建立，指标需要后续在人工核验的数据集上完整运行。不要根据少量样例宣称准确率。

保留旧 L2 集合并从其已有文档、metadata和embedding创建独立cosine候选集合：

```powershell
D:\Anaconda\envs\fastapi311\python.exe -m evaluation.build_cosine_candidate
```

比较 L2/cosine 的 Recall@1/3/5、score分布、延迟、宽泛问题接受率和OOD拒绝率：

```powershell
D:\Anaconda\envs\fastapi311\python.exe -m evaluation.compare_chroma_metrics
```

当前10条精确问题、6条宽泛新闻问题和20条OOD问题支持0.5作为cosine默认阈值。该结论只适用于当前集合、Embedding模型和小规模人工样例，数据变化后必须重跑评测。

## 本轮工程优化

- DB、Redis、CORS、模型、超时、并发和 RAG 参数集中到 Settings。
- 移除源码中的数据库密码，新增无真实 Secret 的 `.env.example`。
- 修复通配 CORS 与 credentials 组合。
- 统一异常响应，禁止堆栈和第三方异常文本泄露。
- 增加 trace_id 和结构化事件日志。
- 增加 AI 超时、Semaphore、SSE 断连取消和安全错误帧。
- Top-K 配置化，增加相关性阈值和来源 metadata。
- 增加完整AI/RAG业务事件日志、cosine候选集合和OOD评测。
- 浏览量更新后主动删除可能陈旧的详情、列表和相关新闻缓存。
- 增加 `/health`、Redis Compose 和最小 RAG 评测。

详细结果见 `backend/docs/today_optimization_report.md` 和 `backend/docs/final_optimization_report.md`。

## 已知限制

- 旧 `news_rag` L2集合仍保留；在线已切换到独立的cosine候选集合。
- 阈值0.5来自当前36条样例，仍需要更大规模、持续维护的人工标注集验证。
- 浏览量更新已有主动失效；项目没有新闻编辑/删除接口，因此不存在对应写路径的失效逻辑。
- 用户消息在调用模型前提交；模型失败时会保留用户消息但不保存 assistant 消息。
- 同一会话并发写消息仍使用 `MAX(message_index)+1`，没有数据库组合唯一约束。
- SSE 断连检测发生在模型产生数据块时；模型长时间无输出由总超时兜底。
- 没有 Alembic、分布式限流、OpenTelemetry 或生产反向代理配置。
- LangChain 社区版 `ChatOpenAI` 和 `Chroma` 适配器已出现弃用警告，本轮遵守“不升级依赖”要求未迁移。
- Hybrid Retrieval 和 rerank 需要新增依赖、索引和评测，本轮主动不实现。
