# 今日集中工程优化报告

> 状态说明：本文记录第一轮工程优化。RAG日志、cosine集合、OOD评测和阈值的最新结果见 `docs/final_optimization_report.md`。

优化日期：2026-08-07

项目定位：面向新闻场景的 AI 后端综合学习项目。本轮目标是提高安全性、可靠性、可观察性和求职展示价值，不将项目描述为生产级系统。

## 1. 修改概览

本轮完成了以下工程改进：

1. 将数据库、Redis、CORS、LLM、并发和 RAG 参数集中到现有 `Settings`。
2. 删除源码中的数据库账号密码，增加无真实 Secret 的 `.env.example`。
3. 修复 `allow_origins=["*"]` 与 credentials 同时启用的问题。
4. 统一参数、业务、数据库和未知异常响应，不再向客户端返回堆栈或第三方异常文本。
5. 增加请求级 trace_id 和 JSON 事件日志。
6. 为 Query Rewrite、Retriever、普通 LLM 和 SSE 增加超时。
7. 使用单进程 `asyncio.Semaphore` 限制 AI 上游并发。
8. SSE 增加客户端断连检测、取消清理和安全错误帧；不保存未完整生成的 assistant 消息。
9. Top-K 配置化，增加 relevance score 阈值和来源 metadata。
10. 增加 `/health`、Redis Compose、pytest 测试和最小 RAG 评测。
11. 保持原有 AI URL、请求体、SSE token 帧和 `X-Session-Id` 兼容。

## 2. 修改文件

| 文件 | 修改内容 | 修改原因 | 影响功能 |
| --- | --- | --- | --- |
| `backend/config/settings.py` | 汇总 DB、Redis、CORS、LLM、并发、RAG、JWT 参数 | 消除硬编码和配置漂移 | 全后端 |
| `backend/config/db_conf.py` | 从 Settings 创建 Engine，配置连接池超时和 recycle | 移除数据库密码硬编码 | 所有数据库接口 |
| `backend/config/cache_conf.py` | Redis 配置化、超时、日志和关闭函数 | Redis 故障可降级和诊断 | 新闻缓存、健康检查 |
| `backend/config/chroma_conf.py` | 只从 Settings 构建入库配置 | 统一离线与在线参数 | RAG 入库 |
| `backend/main.py` | trace_id 中间件、安全 CORS、资源关闭、健康路由 | 可观察性和资源治理 | 所有请求 |
| `backend/utils/observability.py` | JSON 事件日志和 trace ContextVar | 串联一次请求日志 | 全后端日志 |
| `backend/utils/exception.py` | 安全统一错误响应和服务端异常日志 | 防止堆栈、SQL和平台错误泄漏 | 所有异常 |
| `backend/utils/exception_handlers.py` | 注册请求参数校验 handler | 统一 422 响应 | 参数错误 |
| `backend/utils/security.py` | 使用 bcrypt 官方 API | 避免 passlib 与当前 bcrypt 的兼容警告 | 注册、登录 |
| `backend/routers/health.py` | 新增 Backend/DB/Redis 健康检查 | 部署和展示 | `GET /health` |
| `backend/routers/ai_chat.py` | 显式拆分 rewrite、retrieve、generate；超时、Semaphore、SSE、来源、耗时日志 | AI 可靠性和可解释性 | AI聊天 |
| `backend/services/ai_runtime.py` | 全局 AI Semaphore 和等待超时 | 防止单进程无限并发 | AI聊天 |
| `backend/services/model_factory.py` | 配置 temperature、max_tokens、timeout、retry | 避免无限等待和模型漂移 | Query Rewrite、回答 |
| `backend/services/retriever_factory.py` | VectorStore 工厂、Top-K、阈值和分数保留 | 控制无关检索并提供来源 | 在线 RAG |
| `backend/services/RAG_chroma/add_to_chroma.py` | 距离度量读取统一配置 | 避免新建集合参数散落 | 离线入库 |
| `backend/services/update_summary.py` | 摘要调用读取统一 LLM 超时 | 防止摘要无限等待 | 会话摘要 |
| `backend/schemas/ai_chat.py` | 修正 Pydantic 字段描述写法 | 消除弃用警告 | AI Schema |
| `backend/evaluation/*` | 10 条样例、Recall@K 脚本和说明 | 建立最小 RAG 评测基线 | RAG评测 |
| `backend/tests/*`、`backend/pytest.ini` | 配置、JWT、密码、异常、来源、健康检查测试 | 建立关键回归保护 | 自动测试 |
| `backend/.env.example` | 无真实密钥的完整配置模板 | 安全启动和部署说明 | 配置 |
| `compose.yaml` | Redis 容器、healthcheck 和持久卷 | 可复现缓存环境 | Redis |
| `README.md` | 重写启动、架构、配置、RAG、安全和限制说明 | 求职展示和接管 | 文档 |
| `.gitignore` | 忽略评测结果文件 | 避免运行产物进入版本库 | Git |

本地 `backend/.env.local` 仅保存从旧源码迁出的本机数据库连接串，已被 Git 忽略，不属于可提交文件。

## 3. 原来的问题

### 配置与安全

- `config/db_conf.py` 硬编码 MySQL 用户名和密码。
- Redis 主机、端口和 DB 硬编码。
- CORS 同时使用任意来源和 credentials。
- LLM timeout、temperature、max_tokens 未显式设置。
- RAG 切块存在 800/120 与 500/100 两套默认值，Top-K 固定为 5。

### 异常与日志

- `DEBUG_MODE=True` 会把数据库错误和完整 traceback 返回客户端。
- AI SSE 会把第三方异常字符串放入错误帧。
- Redis 使用 `print`，AI 路由也用 `print` 查看链数据。
- 一次请求没有 trace_id，RAG 和 LLM 耗时无法关联。

### AI 与 SSE

- 所有 AI 请求可无限并发访问上游。
- Query Rewrite、Retriever 和生成没有明确的业务超时边界。
- 客户端断连没有显式检查。
- user 消息先保存，模型失败后会留下只有 user 的记录。
- assistant 消息在流结束后保存，但原实现先发送 `[DONE]` 再保存，保存失败时前端已认为完成。

### RAG

- 无条件取 5 个 Document 并全部放入 Prompt。
- 不保留 relevance score，不容易识别低相关召回。
- 回答链内部虽然有 metadata，但接口没有来源字段。
- 没有固定问题集和 Recall@K 评测。

## 4. 修改后的工作流程

```text
用户问题
→ FastAPI trace_id 中间件
→ JWT签名、Claims、黑名单和token version校验
→ 校验或创建会话
→ 等待全局AI Semaphore
→ 读取摘要和最近历史
→ Qwen Query Rewrite（超时）
→ DashScope Embedding（检索超时）
→ Chroma Top-K + relevance score阈值
→ 提取sources并格式化RAG_results
→ ChatPromptTemplate
→ Qwen生成（普通超时或SSE总超时）
→ SSE sources帧 + token帧
→ 完整生成后写入assistant消息
→ 可选更新会话摘要
→ 释放Semaphore、模型流和数据库Session
```

变化集中在四个节点：

- FastAPI 入口增加 trace_id 和安全错误边界。
- 上游 AI 前增加 Semaphore，rewrite/retrieve/generate 分别计时。
- Retriever 增加 Top-K 配置、分数和阈值。
- SSE 只在完整成功后保存 assistant，并在 `finally` 释放槽位和关闭模型流。

## 5. RAG部分

### chunk_size

当前默认 500，单位是 Python 字符长度，不是 token。值过大时一个块可能包含多个主题；值过小时上下文容易被切碎。

### chunk_overlap

当前默认 100，用于保留块边界附近的语义。Overlap 太大会增加重复向量和 Prompt 噪声，太小会丢失跨边界信息。代码强制 overlap 小于 chunk_size。

### Top-K

`RAG_TOP_K=5` 表示初始返回最多 5 个 Document/chunk，不保证是 5 篇不同新闻。该值已从硬编码迁入 Settings。

### Embedding

文档与查询统一使用 `DASHSCOPE_EMBEDDING_MODEL=text-embedding-v3`。Embedding 负责把文本映射为向量，不负责生成答案。

### Chroma

Chroma 保存 `page_content`、向量和新闻 metadata。当前 metadata 包括 `news_id`、标题、分类、发布时间、作者、来源和 chunk 序号。

### similarity/distance

项目代码不自行计算距离，由 Chroma 执行。新建集合配置意图由 `CHROMA_DISTANCE_METRIC` 控制，默认 cosine；但当前历史持久化集合只读检查曾确认实际为 L2。已有索引不会因配置变化自动切换度量。

在线代码调用 `similarity_search_with_relevance_scores`，由 LangChain 把底层 distance 转为 relevance score。数值越大越相关。

### score threshold

默认 `RAG_SCORE_THRESHOLD=0.2`。低于阈值的文本块不会进入 Prompt。当结果为空时，系统 Prompt 要求模型使用固定无资料回答。0.2 是保守初值，需要用固定评测集调整。

### Prompt和LLM

最终 Prompt 仍为 Qxia System Prompt + 历史消息 + 当前用户问题。`{RAG_results}` 现在包含新闻 ID、标题、发布时间、来源、相关度和正文。LLM 统一读取 `LLM_MODEL_ID`，当前为 `qwen3.6-flash`。

### 最小评测

已经建立 `evaluation/rag_cases.json` 和 `evaluation/evaluate_rag.py`。2026-08-07 实际运行 10 条精确样例，过滤后的 Recall@K 为 1.0，未生成回答。该结果仅说明这 10 条样例可召回目标新闻，不能代表开放问题准确率。

## 6. Redis部分

Redis 缓存新闻分类、新闻列表、新闻详情和相关新闻。

| 数据 | Key示例 | TTL |
| --- | --- | ---: |
| 分类 | `news:categories` | 7200秒 |
| 列表 | `news_list:{category}:{page}:{size}` | 1800秒 |
| 详情 | `news:detail:{id}` | 300秒 |
| 相关新闻 | `news:related:{id}:{category}` | 1800秒 |

Cache-Aside 流程：先读 Redis；命中直接返回；未命中查询 MySQL并 SETEX。Redis 连接或 JSON 解析失败时记录结构化 warning，并按 miss 回退 MySQL。

当前缓存主要依赖 TTL，没有新闻更新后的主动批量失效。浏览量自增后详情缓存也可能短暂陈旧，这是中优先级遗留问题。

Redis 可通过项目根目录 `docker compose up -d redis` 启动。Compose 已配置 `redis-cli ping` 健康检查和 `redis_data` 持久卷。

## 7. 数据库部分

### database.sql

本轮没有修改 `database.sql`，因为没有新增 ORM 字段、约束或索引，也不需要对已有数据执行 ALTER TABLE。

原始 SQL 包含：`user`、`user_token`、`news_category`、`news`、`related_news`、`favorite`、`history`、旧 `ai_chat`。

ORM/启动代码还使用：`user_auth_state`、`user_refresh_token`、`user_token_blacklist`、`user_chat_session`、`chat_message`。这些表由 `services/db_bootstrap.py` 在应用启动时 `create_all`。

### 已确认结构差异

- `database.sql` 没有新 JWT 和聊天表；启动代码负责补齐。
- SQL 中保留旧 `user_token` 和 `ai_chat`，当前主 JWT/聊天链不再依赖它们。
- SQL 使用 `INT UNSIGNED/TIMESTAMP`，部分 ORM 使用普通 `Integer/DateTime`。
- 新认证表和 `user_chat_session.user_id` 在 ORM 中没有数据库 ForeignKey，只是逻辑关联。
- `favorite` 的 `(user_id, news_id)` 唯一约束与 ORM 一致。
- `history` 没有 `(user_id, news_id)` 唯一约束，业务层先查后写，存在并发重复风险。

### 事务边界

- 本轮没有大规模重写 CRUD 的 commit 方式。
- RAG 准备成功后才保存 user 消息，检索失败不会留下聊天消息。
- 模型失败时仍可能保留 user 消息，但不会保存错误或不完整 assistant 消息。
- 流式 assistant 保存使用独立短 Session，并在上下文结束后关闭。

完整迁移到 Alembic、增加会话消息唯一约束和消息状态字段，需要独立设计与升级 SQL，不适合与本轮可靠性修改混在一起。

## 8. 并发与SSE

### Semaphore

`services/ai_runtime.py` 创建进程级 `asyncio.Semaphore`，默认并发 4。AI 请求在 Query Rewrite 前获取槽位，整个检索和生成期间持有；普通新闻接口不经过该 Semaphore。等待超过 5 秒返回 503。

该方案易解释、无需新依赖，但只限制单进程。多 worker 或多实例部署需要 Redis 限流或网关级并发治理。

### 超时

- Query Rewrite：60秒。
- Retriever：30秒。
- 普通生成：60秒。
- SSE 模型流总时长：120秒。
- Semaphore 等待：5秒。

超时值全部可以通过环境变量调整。

### 客户端断连

每次收到模型 chunk 后检查 `request.is_disconnected()`。断连或 Starlette 取消生成器时抛出/传播 `CancelledError`，`finally` 关闭模型流、释放 Semaphore 并记录日志。未完整完成的 assistant 文本不会写数据库。

限制：模型长时间不返回任何 chunk 时无法高频检查断连，最终由 120 秒总超时取消。要做到即时断连竞速，需要单独创建断连监听任务并与模型读取竞争，当前没有实现。

## 9. 安全

### JWT

项目原有 JWT 已真正接入注册、登录、刷新、登出和受保护接口。本轮没有重复实现。验证包括签名、issuer、audience、类型、时间、JTI、黑名单和 token version。后端继续兼容 `Bearer token` 和旧前端裸 token。

### 密码

密码使用 bcrypt 哈希。本轮改为 bcrypt 官方 API，兼容现有 `$2b$` 哈希，并移除 passlib 与当前 bcrypt 的运行警告。

### CORS

默认只允许本机 Vue 开发地址。Origin、credentials、Method 和 Header 均明确配置；即使用户配置 `*`，代码也不会同时开启 credentials。

### Secret

数据库 URL、DashScope Key 和 JWT Secret 不再硬编码在源码。`.env.example` 只提供占位符，真实 `.env/.env.local` 被 Git 忽略。

### 异常信息

客户端获得稳定的 `code/message/data/trace_id`，不返回 traceback、SQL、API Key 或第三方原始错误。服务端通过 `logger.error(..., exc_info=...)` 保留完整异常。

## 10. 测试结果

### 已实际测试通过

- `python -m compileall -q .`：通过。
- `pytest -q tests`：7 项通过。
- Settings、应用和 28 条路由导入：通过。
- Docker `compose.yaml` 解析：通过。
- Redis真实 `PING`：通过。
- MySQL真实 `SELECT 1`：通过。
- `GET /health`：200，Backend/Database/Redis 均为 ok。
- 新闻分类接口：200，返回8个分类。
- 不存在用户登录：401，安全错误响应。
- 临时用户注册：200，JWT签发成功。
- 真实 Query Rewrite + DashScope Embedding + Chroma：通过。
- 真实 Qwen SSE：200，1个 sources 帧、46个 token 帧、`[DONE]`，无错误帧。
- 消息保存：数据库实际得到 `user`、`assistant` 两条非空消息。
- 临时测试用户、认证和聊天数据：测试结束后已删除。
- 10条 RAG样例：Recall@K=1.0，未调用答案生成。

真实 AI 冒烟耗时：rewrite 约 5.83 秒，检索约 3.18 秒，生成约 9.19 秒，总计约 18.34 秒。该数据仅代表当次网络和上游状态。

### 只能静态检查

- 浏览器真实关闭页面后的不同断连时点。
- 多个并发请求抢占 Semaphore 的压力表现。
- 反向代理对 SSE 的缓冲和超时。
- Redis 故障下所有新闻页面的前端体验。

### 因外部环境未完整测试

- 长时间 DashScope 超时和平台限流返回。
- Docker Redis 容器实际重启恢复；本机已有 Redis 可用，本轮只验证 Compose 配置。
- 生产域名 CORS 和 HTTPS 反向代理。

## 11. 尚未解决的问题

### 高

1. 当前历史 Chroma 集合实际 L2，而新建集合配置意图为 cosine。需要备份、重建两个候选集合并用同一评测集比较后切换。
2. `database.sql` 不包含新 JWT/聊天表，当前依赖启动时 `create_all`，缺少正式迁移系统。

### 中

1. `MAX(message_index)+1` 在同一会话并发写入时可能竞争，数据库没有组合唯一约束。
2. 用户消息已保存但模型失败时会保留单边消息；目前把它视为真实用户输入，而不是错误数据。
3. 新闻缓存没有主动失效，详情浏览量可能在 TTL 内陈旧。
4. Semaphore 只限制单进程，不覆盖多 worker。
5. SSE 只在收到 chunk 时主动检查断连，静默期间依赖总超时。
6. 相关性阈值需要更多开放问题和人工标注评测。

### 低

1. LangChain community `ChatOpenAI` 和 `Chroma` 适配器有弃用警告；本轮未升级依赖。
2. 仍保留旧 `user_token`、`ai_chat` 表和少量未使用代码。
3. 没有在前端可视化 sources，当前只保证后端提供且旧前端兼容。
4. 没有 Hybrid Retrieval/rerank；在缺少评测基线前不值得增加依赖和复杂度。

## 12. 面试重点

1. **为什么使用 Pydantic Settings？** 统一环境配置、类型校验和默认值，避免多个模块直接读取环境变量导致漂移。
2. **为什么 Secret 不能写源码？** 源码会进入 Git、日志和构建产物；Secret 应通过环境变量或密钥服务注入。
3. **为什么 `allow_origins=["*"]` 不能和 credentials 随意组合？** 浏览器凭证请求需要明确 Origin，通配会造成安全和规范问题。
4. **JWT 为什么还需要黑名单和 token version？** JWT 本身无状态，黑名单用于撤销单个令牌，version 用于批量使旧令牌失效。
5. **为什么密码改用 bcrypt？** bcrypt 是带盐的慢哈希，适合密码存储；验证时不需要解密。
6. **Semaphore解决什么？** 限制单进程同时访问昂贵上游的请求数，避免连接、额度和内存无限增长。
7. **Semaphore为什么不是分布式限流？** 每个进程有独立计数，多实例需要共享 Redis 或网关状态。
8. **普通超时和SSE超时有什么区别？** 普通调用等待单个结果；SSE 是长流，需要约束整个迭代并处理取消。
9. **断连后为什么不保存assistant？** 内容不完整且用户没有收到完成信号，保存会污染历史上下文。
10. **为什么user消息仍可能单独存在？** 它是已经提交的真实输入；要表示生成失败需新增消息状态或请求表，这涉及数据库迁移。
11. **Vector Store和Retriever的区别？** Vector Store负责向量存储/搜索，Retriever提供面向应用链的统一检索接口和参数。
12. **Top-K是文章数还是chunk数？** 是 Document/chunk 数，一篇新闻可能贡献多个块。
13. **distance和relevance score有什么区别？** distance通常越小越近；LangChain relevance score转换后通常越大越相关。
14. **阈值为什么不能凭经验定死？** Embedding、距离度量和数据分布都会改变分数，需要固定测试集和失败样本调参。
15. **Recall@K衡量什么？** 目标文档是否出现在前K个召回中，只评价检索，不代表最终答案正确。
16. **为什么不做Hybrid Retrieval？** 会新增分词、索引和融合权重；没有评测基线时无法证明收益。
17. **Cache-Aside如何工作？** 先读缓存，miss读数据库并回填；写操作需要失效或等待TTL。
18. **trace_id有什么用？** 把入口、鉴权、检索、模型、SSE和异常日志关联为一次请求。
19. **AsyncSession为何在SSE中要谨慎？** 长连接可能让请求级Session长期存在；保存阶段使用短Session可降低连接池占用。
20. **为什么没有直接修改database.sql和ORM加约束？** 约束变化需要评估历史脏数据、提供ALTER脚本和回滚方案，不能与当天可靠性改造混做。

## 13. 未来学习代码的顺序

### 第一批：必须重新看

1. `config/settings.py`
2. `main.py`
3. `routers/ai_chat.py`
4. `services/ai_runtime.py`
5. `services/model_factory.py`
6. `services/retriever_factory.py`
7. `services/get_retrievel.py`
8. `utils/create_prompt.py`
9. `config/system_prompt.txt`
10. `crud/ai_chat.py`
11. `config/db_conf.py`
12. `config/cache_conf.py`
13. `utils/jwt_tokens.py`
14. `crud/users.py`

### 第二批：理解用途即可

1. `services/RAG_chroma/add_to_chroma.py`
2. `config/chroma_conf.py`
3. `services/update_summary.py`
4. `routers/news.py` 与 `crud/news_cache.py`
5. `cache/news_cache.py`
6. `routers/health.py`
7. `utils/exception.py`
8. `evaluation/evaluate_rag.py`
9. `models/*.py` 与 `database.sql`

### 第三批：暂时跳过

1. 前端页面样式和静态资源。
2. Chroma HNSW底层实现。
3. LangChain Runnable内部源码。
4. 未使用的旧 Token/AI Chat代码清理。
5. LangGraph、多Agent、GraphRAG、MCP和分布式追踪平台。
