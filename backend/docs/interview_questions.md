# 项目面试问题清单

> 状态说明：本文主体基于工程优化前的只读审计；本轮新增能力对应的面试题和参考答案见 `docs/today_optimization_report.md` 第 12 节。

本文档基于当前仓库代码整理，用于 Python 后端和 AI 应用开发岗位的项目复盘。回答时应先讲通用原理，再说明本项目如何实现，最后主动指出当前风险。

证据标签：

- 【代码已确认】：当前仓库可直接证明。
- 【合理推断】：结合框架行为可以推断，但项目代码没有显式实现细节。
- 【暂时无法确认】：需要运行实验、外部平台配置或生产数据才能确认。

## 一、必须脱离代码讲清楚的知识

| 主题 | 至少需要讲清楚的内容 | 本项目入口 |
| --- | --- | --- |
| FastAPI 依赖注入 | `Depends` 如何提供当前用户和数据库会话；请求结束后资源如何释放 | `utils/auth.py:get_current_user`；`config/db_conf.py:get_db` |
| AsyncSession | 异步会话、事务边界、`flush`、`commit`、`rollback`、并发安全 | `config/db_conf.py:get_db`；`crud/*.py` |
| JWT | 签名、Claims、Access Token、Refresh Token、过期校验、吊销与版本控制 | `utils/jwt_tokens.py`；`crud/users.py`；`models/users.py` |
| Redis Cache-Aside | 先读缓存、未命中查数据库、回填缓存、写操作后失效 | `crud/news_cache.py`；`cache/news_cache.py`；`config/cache_conf.py` |
| RAG | 离线建库与在线问答两个阶段，以及检索上下文为什么能降低幻觉 | `services/RAG_chroma/add_to_chroma.py`；`services/retriever_factory.py`；`routers/ai_chat.py` |
| 文本切块 | `chunk_size`、`chunk_overlap`、分隔符和文档结构对召回的影响 | `config/chroma_conf.py`；`services/RAG_chroma/add_to_chroma.py:_build_documents_for_news` |
| Embedding | 文本如何映射为向量；Embedding 模型与生成式 LLM 的职责差异 | `services/RAG_chroma/add_to_chroma.py:_build_embedding_model`；`services/retriever_factory.py:get_news_retriever` |
| Vector Store 与 Retriever | Vector Store 负责存储和相似度搜索，Retriever 提供面向链的统一检索接口 | `services/RAG_chroma/add_to_chroma.py:_build_vector_store`；`services/retriever_factory.py:get_news_retriever` |
| Top-K 与距离度量 | `k` 表示文本块数，不是文章数；余弦、L2 距离的方向和阈值含义不同 | `services/retriever_factory.py:28-36`；当前持久化集合为 L2，见 `docs/rag_flow.md` |
| Prompt 与上下文 | System Prompt、历史消息、检索资料和当前问题各自承担什么职责 | `utils/create_prompt.py`；`config/system_prompt.txt`；`config/user_prompt.txt`；`routers/ai_chat.py` |
| LCEL | `RunnablePassthrough`、字典映射、Prompt、模型和输出解析器如何用 `|` 串联 | `routers/ai_chat.py:141-162` |
| SSE | HTTP 长连接、`text/event-stream`、事件格式、客户端增量解析和断连处理 | `routers/ai_chat.py:178-225`；`../frontend/src/views/AIChat.vue:148-275` |
| 并发与一致性 | 同一会话并发提问、消息顺序、事务提交、SSE 中断后的半完成状态 | `routers/ai_chat.py`；`crud/ai_chat.py` |
| RAG 评测 | 召回率、上下文相关性、答案忠实度、引用正确性和固定测试集 | 当前仓库未实现，属于改进项 |

## 二、只需知道用途的内容

- `Schema`：负责请求和响应数据校验，重点知道 `schemas/ai_chat.py`、`schemas/users.py` 的边界，不必背所有字段。
- SQLAlchemy ORM 模型：知道表之间的关系、主键和关键索引即可，重点关注用户、新闻、会话和聊天消息模型。
- 前端 Pinia：知道 Token、用户信息和聊天状态如何保存即可，不必深入页面样式。
- Chroma 内部 HNSW 实现：知道它负责近似最近邻搜索即可；除非岗位强调向量数据库，不必讲源码实现。
- DashScope/OpenAI 兼容协议：知道项目通过兼容端点调用 Qwen，以及 LangChain 如何封装即可。
- Pydantic Settings：知道 `.env` 到 Settings 对象的配置链即可。

## 三、当前可以跳过的内容

- 页面 CSS、静态资源和非核心视觉交互。
- Chroma HNSW 索引底层源码和数学证明。
- LangChain 每个 Runnable 的内部源码。
- 与项目当前调用链无关的 Agent、复杂 Tool Calling 和多智能体设计。
- 在基础 RAG 尚未建立评测基线前的大规模 LangGraph 改造。

## 四、20 个高概率面试问题

### 1. 这个项目从启动到处理请求的主链路是什么？

**回答要点：** `main.py` 创建 FastAPI 应用，在启动阶段初始化资源并注册路由；请求进入具体 Router 后，通过 `Depends` 注入当前用户、`AsyncSession` 或 Redis，再进入 CRUD、RAG 或 LLM，最后返回 JSON 或 SSE。【代码已确认】

**代码依据：** `main.py:8-40`；`config/db_conf.py:40-49`；`utils/auth.py:17-27`。

### 2. `AsyncSession` 为什么适合这个项目？事务边界在哪里？

**回答要点：** 异步数据库驱动避免在网络 I/O 时阻塞事件循环。`get_db` 为请求提供会话并在结束时关闭；当前部分 CRUD 自行 `commit`，因此事务边界分散，跨多次写入时难以保证原子性。【代码已确认】

**代码依据：** `config/db_conf.py:21-49`；`crud/users.py`；`crud/ai_chat.py`。

### 3. 用户登录后，后端如何验证身份？

**回答要点：** 登录接口校验账号密码并签发 Access/Refresh Token；受保护接口从 Authorization Header 读取 Bearer Token，验证签名、过期时间、Token 类型、黑名单和用户状态，然后查询当前用户。【代码已确认】

**代码依据：** `routers/users.py:49-82`；`utils/auth.py:17-27`；`utils/jwt_tokens.py:68-137`；`crud/users.py:127-257`。

### 4. Access Token、Refresh Token、黑名单和 Token Version 分别解决什么问题？

**回答要点：** Access Token 生命周期短，用于业务请求；Refresh Token 用于换取新令牌；黑名单用于立即吊销单个令牌；版本号用于一次性使某个用户旧版本令牌整体失效。当前代码已经包含 Refresh、黑名单和版本控制路径，但仍应说明并发刷新和存储清理策略。【代码已确认】

**代码依据：** `utils/jwt_tokens.py:68-137`；`crud/users.py:170-257`；`models/users.py:65-125`。

### 5. 新闻列表缓存采用什么模式？

**回答要点：** 采用 Cache-Aside：路由或 CRUD 先按参数组成 Key 读取 Redis；未命中后查询数据库并写回缓存；新闻相关写操作后需要删除相应 Key。优势是实现简单，风险是失效范围、短暂不一致和缓存击穿。【代码已确认】

**代码依据：** `crud/news_cache.py:12-125`；`cache/news_cache.py:7-99`；`config/cache_conf.py:6-53`。

### 6. Redis 不可用时，系统会发生什么？

**回答要点：** 应区分“缓存失败降级到数据库”和“缓存异常直接导致接口失败”。当前代码存在缓存封装，但生产环境的超时、熔断和完整降级效果需要故障注入才能确认。【暂时无法确认】

**代码依据：** `config/cache_conf.py:22-53`；`cache/news_cache.py`；`crud/news_cache.py`。

### 7. 请完整讲一遍本项目 RAG 的离线阶段。

**回答要点：** 从 MySQL 批量读取新闻，拼接标题、摘要和正文，使用递归字符切分器生成 `Document` 文本块，调用 DashScope Embedding 生成向量，再写入本地持久化 Chroma，同时在 metadata 中保存新闻标识等信息。【代码已确认】

**代码依据：** `services/RAG_chroma/add_to_chroma.py:52-133,175-240`；`config/chroma_conf.py:8-51`。

### 8. 在线 RAG 问答如何执行？

**回答要点：** 用户问题进入 AI 路由；项目先结合会话摘要和近期消息改写检索 Query；Retriever 将 Query 交给 Chroma，Chroma 内部调用 Embedding；返回前五个文本块后格式化为 `{RAG_results}`，Prompt 再与历史消息和当前问题组合，最后调用 Qwen，并通过 SSE 或普通响应返回。【代码已确认】

**代码依据：** `routers/ai_chat.py:94-269`；`services/get_retrievel.py:11-26`；`services/retriever_factory.py:10-36`；`utils/create_prompt.py`。

### 9. Embedding 模型与聊天模型有什么区别？

**回答要点：** Embedding 模型输出固定维度的数值向量，用于语义检索；聊天模型根据上下文逐 Token 生成自然语言。本项目 Embedding 为 `text-embedding-v3`，Chat Model 当前配置为 `qwen3.6-flash`，两者不可互换。【代码已确认】

**代码依据：** `.env`；`config/settings.py:10-45`；`services/model_factory.py:8-32`；`services/retriever_factory.py:16-26`。

### 10. 项目代码在哪里真正触发问题向量化？

**回答要点：** 【代码已确认】项目没有在路由中直接调用 `embed_query`，而是由 `get_news_retriever` 创建 Chroma Retriever 并接入链。【合理推断】链执行 Retriever 后，LangChain 的 `VectorStoreRetriever` 调用 Chroma 搜索，Chroma 再调用已绑定的 `DashScopeEmbeddings.embed_query`。

**代码依据：** `services/retriever_factory.py:10-36`；触发点 `routers/ai_chat.py:153-162`。

### 11. `k=5` 表示五篇新闻还是五个文本块？

**回答要点：** 表示 Retriever 返回最多五个 `Document` 文本块，不保证对应五篇不同新闻；一篇新闻可以切成多个块。当前库运行检查显示 403 条新闻均只有一个块，这是当前数据状态，不是代码契约。【代码已确认】

**代码依据：** `services/retriever_factory.py:34-36`；`services/RAG_chroma/add_to_chroma.py:106-133`。

### 12. `chunk_size` 和 `chunk_overlap` 怎样影响回答质量？

**回答要点：** 块过大时主题混杂、检索不精确且上下文成本高；块过小时语义不完整。Overlap 用于保留边界上下文，但过大会制造重复召回和存储开销。当前存在 `800/120` 默认值与 `500/100` 加载值，说明参数来源尚未统一。【代码已确认】

**代码依据：** `config/chroma_conf.py:12-45`；`services/RAG_chroma/add_to_chroma.py:82-90`。

### 13. Vector Store 和 Retriever 有什么区别？

**回答要点：** Chroma Vector Store 管理向量、文档和 metadata，并执行距离搜索；Retriever 是 LangChain 的统一检索接口，封装 `search_type` 和 `k`，使其可以直接接入 LCEL。当前项目使用 `as_retriever` 创建 Retriever。【代码已确认】

**代码依据：** `services/RAG_chroma/add_to_chroma.py:64-79`；`services/retriever_factory.py:28-36`。

### 14. 当前项目使用余弦相似度还是 L2 距离？

**回答要点：** 入库代码显式传入 `hnsw:space=cosine`，但对当前持久化集合的只读检查显示 collection metadata 为空、SQLite segment 配置为 `space:l2`。因此当前实际库按 L2 距离工作，代码意图与持久化状态不一致；不能只根据源码宣称使用余弦。【代码已确认】

**代码依据：** `services/RAG_chroma/add_to_chroma.py:73-78`；运行证据与解释见 `docs/rag_flow.md`。

### 15. 为什么需要 Query Rewrite？它可能造成什么问题？

**回答要点：** 多轮对话中的“它”“这个事件”等问题缺少独立语义，改写可以结合摘要和历史消息生成可检索 Query。风险是摘要错误、历史噪声或模型改写偏离原问题，从而降低召回率，因此应记录原问题与改写 Query 并评测。【代码已确认】

**代码依据：** `routers/ai_chat.py:129-162`；`services/get_retrievel.py:8-26`；`config/retrievel_rag_sysytem.txt:1-2`。

### 16. `{RAG_results}` 如何进入最终 Prompt？

**回答要点：** Retriever 输出 `Document` 列表，经格式化函数提取 `page_content` 并拼接；LCEL 字典将其挂到 `RAG_results` 键，`ChatPromptTemplate` 在执行时渲染同名占位符。变量名、输入结构或格式化结果为空都会导致参考资料区域为空。【代码已确认】

**代码依据：** `routers/ai_chat.py:76-88,145-162`；`config/system_prompt.txt:1-9`。

### 17. 这段 LCEL 链的执行顺序是什么？

**回答要点：** 输入先分流到检索结果格式化、历史消息、摘要和原始问题等字段；字段字典传入 Prompt 模板；模板产生消息列表后传给 Chat Model；非流式路径再解析模型输出，流式路径遍历模型产生的 Chunk。【代码已确认】

**代码依据：** `routers/ai_chat.py:153-162,178-269`。

### 18. 为什么前端用 Fetch 读取 SSE，而不是原生 EventSource？

**回答要点：** 原生 EventSource 主要支持 GET，且不方便携带 JSON 请求体和自定义 Authorization Header；当前接口是带 Token、会话 ID 和问题正文的 POST，所以前端用 Fetch 读取 `ReadableStream` 并自行解析 SSE 数据。【代码已确认】

**代码依据：** `../frontend/src/views/AIChat.vue:148-275`；`routers/ai_chat.py:94-225`。

### 19. SSE 中途断开时，消息和数据库状态会怎样？

**回答要点：** 【代码已确认】用户消息在开始生成前保存，助手消息在流结束并聚合完整文本后保存；代码没有显式的断连检测分支。【暂时无法确认】客户端在不同断连时点是否取消上游调用以及 Session 的具体收尾行为，需要专项验证，不能假定框架会自动保证完整一致性。

**代码依据：** `routers/ai_chat.py:168-225`；`crud/ai_chat.py:71-101`。

### 20. 你会怎样评估并改进这个 RAG？

**回答要点：** 先建立包含问题、标准新闻、标准答案的固定集；分别评估检索 Recall@K、MRR、上下文相关性、答案忠实度和引用正确性；记录原问题、改写 Query、召回块、距离、Prompt 版本、模型和耗时；再单变量比较切块、K、距离度量和 Query Rewrite。当前仓库没有自动化 RAG 评测基线。【代码已确认】

**代码依据：** 当前仅有 `test_main.http:1-9`，未覆盖 RAG；路线见 `docs/modification_roadmap.md`。

## 五、建议的项目介绍顺序

1. 用一分钟说明业务：新闻浏览、用户系统、收藏历史和基于新闻库的 AI 问答。
2. 用两分钟说明后端：FastAPI Router、依赖注入、AsyncSession、JWT、Redis Cache-Aside。
3. 用三分钟说明 RAG：MySQL 新闻离线入库 Chroma，在线 Query Rewrite、Top-K 检索、Prompt、Qwen 和 SSE。
4. 主动说明两个真实问题：当前 Chroma 度量配置漂移；SSE 中断与消息持久化一致性缺少验证。
5. 最后说明改进方法：统一配置、增加超时和结构化日志、建立 RAG 评测集，不宣称尚未实现的能力。
