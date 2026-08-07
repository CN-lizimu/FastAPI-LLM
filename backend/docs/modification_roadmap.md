# 项目修改路线图

> 状态说明：本文是 2026-08-07 工程优化前提出的路线图，其中部分 P0/P1 项目已完成。当前完成情况和剩余问题以 `docs/today_optimization_report.md` 为准。

本文档只记录后续修改建议，本轮未实施任何生产代码、配置、数据库、Docker 或依赖变更。每一项都应作为独立任务处理，并在修改前重新确认复现方式和验收标准。

证据标签：

- 【代码已确认】：当前仓库可直接证明。
- 【合理推断】：根据调用链和框架行为推断，仍需实验验证。
- 【暂时无法确认】：缺少运行、平台或生产环境证据。

## 一、优先级路线图

| 优先级 | 问题 | 证据文件 | 需要修改的文件 | 最小修改方案 | 预计影响 | 是否适合当前 5 天完成 |
| --- | --- | --- | --- | --- | --- | --- |
| P0 | Chroma 距离度量配置漂移：入库代码指定 cosine，但当前持久化集合实际为 L2【代码已确认】 | `services/RAG_chroma/add_to_chroma.py:73-78`；`config/chroma_conf.py:8-51`；`docs/rag_flow.md` 的运行证据 | `config/chroma_conf.py`、`services/RAG_chroma/add_to_chroma.py`、`services/retriever_factory.py`、新增针对 Chroma 配置的测试或校验脚本 | 先备份并验证数据；在唯一工厂中固定 collection、目录、Embedding 和 metric；启动或入库前检查现有集合 metadata，不一致时明确失败，另行重建集合 | 直接影响召回排序；重建向量库耗时但不改变 MySQL 新闻 | 是，前提是单独备份和回归检索结果 |
| P0 | 主 LLM 调用缺少明确的超时和错误分类；入库 Embedding 已有三次重试【代码已确认】 | `services/model_factory.py:20-32`；`routers/ai_chat.py:178-269`；`services/RAG_chroma/add_to_chroma.py:52-61` | `config/settings.py`、`services/model_factory.py`、`routers/ai_chat.py` | 增加统一超时配置；只对可重试网络错误做有限次数退避；将平台错误映射为稳定的业务错误，不向客户端泄漏内部异常 | 避免请求无限等待，提高故障可诊断性 | 是 |
| P0 | 【代码已确认】SSE 没有显式断连处理；【暂时无法确认】不同断连时点的上游取消和 Session 收尾行为 | `routers/ai_chat.py:168-225`；`../frontend/src/views/AIChat.vue:148-275` | `routers/ai_chat.py`、`crud/ai_chat.py`、`../frontend/src/views/AIChat.vue`、相关测试 | 在生成循环中检测断连/取消；用 `try/except/finally` 区分完成、失败和取消；只在定义清晰的状态下保存助手消息 | 防止资源继续消耗和出现半完成会话 | 是，但需前后端联调 |
| P0 | 用户消息先提交、助手消息后提交，生成失败会留下不成对消息【代码已确认】 | `routers/ai_chat.py:168-225`；`crud/ai_chat.py:71-101` | `routers/ai_chat.py`、`crud/ai_chat.py`、可能涉及 `models/ai_chat.py` | 不立即改数据库结构时，先明确允许“用户消息成功、助手消息失败”的状态，并记录失败原因；若需要严格状态机，再单独设计消息状态字段和迁移 | 改善聊天记录一致性和错误恢复 | 部分适合；数据库迁移不建议塞进同一任务 |
| P0 | AI 路由直接把第三方异常字符串返回客户端，存在内部信息泄漏和接口不稳定风险【代码已确认】 | `routers/ai_chat.py:165-166,195-203,227-230`；`utils/exception.py:13-104` | `main.py`、`routers/ai_chat.py`、`utils/exception.py` | 复用全局异常机制并建立稳定错误码；客户端仅返回简短信息和 trace_id；完整堆栈只写服务端日志 | 提升安全性和前端错误处理稳定性 | 是 |
| P1 | RAG 参数分散且存在默认值覆盖，难以确认生效值【代码已确认】 | `config/chroma_conf.py:12-48`；`services/retriever_factory.py:34-36`；`services/RAG_chroma/add_to_chroma.py:82-90` | `config/settings.py`、`config/chroma_conf.py`、`services/retriever_factory.py`、`services/RAG_chroma/add_to_chroma.py`、`.env.example` | 将 chunk、overlap、K、search type、collection、persist path 和 metric 汇总到 Settings；业务模块只读配置对象；启动日志输出非敏感生效值 | 降低配置漂移，便于面试解释和调参 | 是 |
| P1 | 缺少端到端 RAG 评测，无法证明参数改动改善了效果【代码已确认】 | 当前仅有 `test_main.http:1-9`，没有 RAG 评测文件 | 后续在 `tests/` 下新增独立评测数据与脚本；不必先修改生产代码 | 建立 20 至 50 条固定问题集，记录目标新闻 ID；先评估 Recall@5，再增加答案忠实度和引用准确性 | 为切块、K、Query Rewrite 和 metric 调整提供依据 | 是，可先完成小规模基线 |
| P1 | 日志缺少统一结构和贯穿请求的 trace_id【代码已确认】 | `main.py:8-40`；`routers/ai_chat.py:90-230`；`services/update_summary.py:85-139` | `main.py`、新增日志/中间件模块、`routers/ai_chat.py`、`services/model_factory.py` | 使用 JSON 或固定字段日志；中间件生成/透传 trace_id；记录 user/session、模型、检索耗时、召回文档 ID、Token 阶段和错误类别，避免记录完整 Token 与敏感正文 | 显著提高 RAG 和 SSE 排障能力 | 是 |
| P1 | 同一会话并发提问时，消息序号或摘要更新可能竞争【合理推断】 | `crud/ai_chat.py:71-101,126-154`；`routers/ai_chat.py:168-261` | `crud/ai_chat.py`、`routers/ai_chat.py`、数据库索引/约束迁移视方案而定 | 先编写并发测试；最小方案是在会话维度串行化或使用数据库唯一约束与重试；不要仅依赖“查询最大值再加一” | 防止乱序、重复序号和摘要覆盖 | 仅测试与最小锁策略适合；完整迁移可能超出 5 天 |
| P1 | Redis 缓存失效范围和故障降级缺少自动化验证【代码已确认】 | `crud/news_cache.py:12-125`；`cache/news_cache.py:7-99`；`config/cache_conf.py:22-53` | 上述文件及缓存测试 | 为命中、未命中、写后失效、Redis 超时和序列化失败建立测试；Key 构造统一封装；明确降级到数据库的错误边界 | 降低脏缓存和缓存故障放大风险 | 是 |
| P1 | CORS 使用宽泛配置，不适合生产环境【代码已确认】 | `main.py:21-34` | `config/settings.py`、`main.py`、`.env.example` | 按环境配置允许的 Origin、Method 和 Header；生产环境禁止通配来源与携带凭证的危险组合 | 提升浏览器接口安全性 | 是 |
| P1 | Query Rewrite 与最终回答缺少可观察中间结果【代码已确认】 | `routers/ai_chat.py:123-162`；`services/get_retrievel.py:8-26` | `routers/ai_chat.py`、日志模块、可选评测脚本 | 日志记录原问题、改写 Query 的摘要/哈希、召回 ID 与距离、是否命中；不要写入敏感完整对话 | 可以定位“改写偏离”还是“向量召回失败” | 是 |
| P2 | JWT 刷新并发、黑名单 TTL 和 Token Version 边界需要测试【代码已确认】 | `utils/jwt_tokens.py:68-137`；`crud/users.py:170-257` | `crud/users.py`、`utils/jwt_tokens.py`、鉴权测试 | 增加重复刷新、旧 Refresh Token、版本递增、登出后访问等测试；发现竞态后再决定原子操作方案 | 提高登录态安全性和可解释性 | 是，优先测试而非重写 |
| P2 | 数据库连接、池参数和环境配置需要生产化核查【代码已确认】 | `config/db_conf.py:15-49`；`config/settings.py:10-56`；`.env` | `config/settings.py`、`config/db_conf.py`、`.env.example` | 只从 Settings 创建 URL；集中配置 pool size、overflow、timeout 和 recycle；日志隐藏密码；补充不同环境示例 | 改善部署稳定性，避免配置泄漏 | 是 |
| P2 | 当前接口测试文件与真实路由不一致，核心调用链缺少回归测试【代码已确认】 | `test_main.http:1-9` | `test_main.http`，后续可新增 `tests/` | 先修正启动与健康检查请求，再覆盖登录、新闻缓存、AI 非流式错误路径和 RAG Retriever；外部服务使用可控替身 | 降低后续修改回归风险 | 是，但应限定第一批范围 |
| P2 | 缺少 Docker 和部署定义【代码已确认】 | 仓库未发现 `Dockerfile`、Compose、Nginx 配置 | 后续新增 `Dockerfile`、`compose.yaml`、部署说明；生产代码通常无需改 | 固定 Python 版本和启动命令；MySQL、Redis、Chroma 数据卷与健康检查分开配置；密钥只通过环境变量注入 | 提升可复现部署能力 | 是，可完成开发版，不等同于生产就绪 |
| P2 | 前端首页可能重复触发数据加载【代码已确认】 | `frontend/src/views/Home.vue` 的生命周期调用 | `frontend/src/views/Home.vue` | 保留一个明确的数据加载入口，并加请求去重或 loading 防抖；修改前用网络面板复现 | 减少重复数据库和缓存请求 | 是 |
| P3 | 核心链路缺少解释“框架内部实际调用”的注释和文档【代码已确认】 | `services/retriever_factory.py`；`routers/ai_chat.py`；`services/RAG_chroma/add_to_chroma.py` | 优先更新 `docs/`；生产代码只在非自解释位置补少量注释 | 注释说明 Retriever 触发 `embed_query`、`k` 是块数、SSE 保存时点；避免逐行翻译 | 提升接管和面试准备效率 | 是 |

## 二、建议的五天执行顺序

此顺序强调“先建立证据，再修改行为”。每天仍应只选择一个明确问题作为独立任务提交。

### 第 1 天：冻结基线并验证 Chroma

- 记录当前 Settings、依赖版本、collection、持久化目录、向量数量、维度和实际距离度量。
- 建立一组最小检索问题，保存当前 Top-5 新闻 ID 和距离。
- 只处理 metric 配置漂移，不同时调整切块和 K。

**验收结果：** 新建或重建的集合 metadata 与代码配置一致；同一问题重复检索结果稳定；原 MySQL 数据未改变。

### 第 2 天：建立最小 RAG 评测

- 准备 20 至 50 条新闻问题和目标新闻 ID。
- 计算 Recall@5，并记录改写前后 Query 的差异。
- 将失败样本分为数据缺失、切块问题、改写偏离、Embedding/距离问题和 Prompt 问题。

**验收结果：** 参数修改前后可以用同一数据集比较，不再只凭聊天体验判断。

### 第 3 天：处理 LLM 超时与 SSE 中断

- 先只增加超时和错误分类，再单独处理断连取消。
- 验证正常流、模型 400、模型超时、客户端主动取消四种情况。
- 明确用户消息和助手消息在每种情况下的保存状态。

**验收结果：** 请求不会无限挂起；前端获得稳定错误结构；日志能区分取消、超时和平台错误。

### 第 4 天：结构化日志与 trace_id

- 给每个请求生成 trace_id，并在响应头和错误响应中返回。
- AI 链路记录模型名、原 Query、改写 Query 标识、召回 ID、耗时和完成状态。
- 检查日志不包含密码、完整 Token 和不必要的用户隐私数据。

**验收结果：** 一次问答可以通过单个 trace_id 从路由追踪到检索、模型、SSE 和消息保存。

### 第 5 天：缓存、鉴权和部署回归

- 优先补测试，不在同一天重构全部模块。
- 验证 Redis 命中/失效、Refresh Token 重放、CORS 环境配置和基础 Docker 启动。
- 更新 README 和面试说明，记录仍未解决的问题。

**验收结果：** 核心测试可重复运行；开发环境可以按文档启动；未把开发配置误称为生产就绪。

## 三、每次修改前的固定检查单

1. 给出可复现输入、当前输出和期望输出。
2. 列出涉及文件与明确不修改的文件。
3. 先写或保存能够失败的验证样例。
4. 采用最小修改，不同时调整多个 RAG 参数。
5. 运行单元测试、接口测试和必要的只读数据检查。
6. 使用 `git diff --stat` 和 `git diff` 检查是否混入无关变更。
7. 记录修改前后的调用链、配置值和实际测试结果。

## 四、当前不建议实施的改造

- 不建议在 RAG 召回基线尚未建立时直接引入 LangGraph；基础链路问题不会因工作流框架而自动消失。
- 不建议同时迁移数据库、Redis、向量库和模型供应商；这会让失败原因无法归因。
- 不建议为了“统一风格”大规模移动目录或重写所有 CRUD。
- 不建议直接升级 LangChain、Chroma 或 SQLAlchemy；应先建立测试并单独评估版本兼容性。
- 不建议把异常堆栈、API Key、完整 Token、数据库连接串写入前端响应或普通日志。

## 五、仍无法确认的事项

- 【暂时无法确认】生产部署是否有反向代理超时、连接数和 SSE 缓冲配置，因为仓库没有部署文件。
- 【暂时无法确认】Redis 故障时所有新闻接口是否都能正确降级，需要故障注入测试。
- 【暂时无法确认】同一用户对同一会话并发提问时是否必然出现消息乱序，需要并发压测。
- 【暂时无法确认】DashScope 在当前账号和区域下的限流、超时及重试边界，需要结合平台响应头和官方配置验证。
- 【暂时无法确认】改变 Chroma metric、切块或 K 后能否提高业务问答准确率，必须通过固定评测集验证。
