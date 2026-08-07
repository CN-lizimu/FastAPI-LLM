# 项目文件地图

> 状态说明：本文是 2026-08-07 工程优化前的只读审计快照，路径和职责仍可参考，但部分行号与实现状态已变化。请结合 `docs/today_optimization_report.md` 阅读。

## 文档目的

本文面向 Python 后端和 AI 应用开发岗位面试，说明当前仓库中哪些文件必须掌握、哪些只需知道用途、哪些可以暂时跳过。

证据标签：

- 【代码已确认】：可由当前仓库或当前已安装运行环境直接证明。
- 【合理推断】：根据当前框架行为推断，仓库没有显式配置。
- 【暂时无法确认】：现有代码、配置和只读运行信息不足以确定。

## 核心目录

```text
FastAPI-LLM/
├─ AGENTS.md
├─ README.md
├─ backend/
│  ├─ main.py
│  ├─ routers/
│  ├─ schemas/
│  ├─ services/
│  │  └─ RAG_chroma/
│  ├─ crud/
│  ├─ models/
│  ├─ config/
│  ├─ cache/
│  ├─ utils/
│  ├─ chroma_db/
│  ├─ docs/
│  ├─ requirements.txt
│  └─ test_main.http
└─ frontend/
   ├─ src/config/api.js
   ├─ src/store/
   ├─ src/views/
   ├─ src/router/index.js
   └─ package.json
```

目录职责：

| 路径 | 职责 |
| --- | --- |
| `backend/main.py` | 创建 FastAPI 应用，注册启动事件、异常处理、CORS 和路由。 |
| `backend/routers/` | HTTP API 入口，负责参数依赖、业务编排和响应。 |
| `backend/schemas/` | Pydantic 请求与响应结构、字段别名和基础校验。 |
| `backend/services/` | LLM、Retriever、会话摘要和启动建表等服务。 |
| `backend/services/RAG_chroma/` | 新闻切块、Embedding、Chroma 入库和只读检查。 |
| `backend/crud/` | SQLAlchemy 查询、写入以及新闻 Cache-Aside 逻辑。 |
| `backend/models/` | SQLAlchemy ORM 表模型、字段、索引和约束。 |
| `backend/config/` | MySQL、Redis、Chroma、LLM、JWT 和 Prompt 配置。 |
| `backend/cache/` | 新闻 Redis key、TTL 和缓存访问封装。 |
| `backend/utils/` | JWT、鉴权、密码、Prompt、异常和统一响应。 |
| `frontend/src/config/api.js` | 前端后端地址及 AI 接口地址。 |
| `frontend/src/store/` | Pinia 状态与 Axios 请求。 |
| `frontend/src/views/AIChat.vue` | AI 输入、POST 流式请求和 SSE 解析。 |
| `backend/test_main.http` | 当前唯一接口测试文件，但内容已过时。 |

【代码已确认】仓库不存在 Dockerfile、Compose、Nginx、Alembic、pytest 测试目录或 Python lock 文件。部署方式仅见根目录 `../README.md:61-113` 的手工启动说明。

## A 级：必须掌握

| 文件路径 | 文件职责 | 核心类/函数 | 上游调用者 | 下游依赖 | 面试价值与当前风险 |
| --- | --- | --- | --- | --- | --- |
| `main.py:8-40` | 应用创建、启动建表、异常、CORS、路由 | `app`、`startup_db_bootstrap` | Uvicorn | `db_bootstrap`、各 router | 必须讲清启动流程；`allow_origins=["*"]` 是生产风险。 |
| `config/db_conf.py:15-49` | Async Engine、连接池、Session 生命周期 | `async_engine`、`AsyncSessionLocal`、`get_db` | FastAPI Depends、入库脚本 | MySQL/aiomysql | 必须讲清事务；数据库 URL 硬编码，CRUD 又自行 commit。 |
| `config/settings.py:10-56` | LLM、Embedding、JWT 中央配置 | `Settings`、`get_settings` | 模型/JWT 服务 | `.env` | 模型配置主来源；Chroma、Redis、DB 尚未统一进入这里。 |
| `routers/users.py:36-108` | 注册、登录、刷新、登出和用户信息 | `register`、`login`、`refresh_token`、`logout` | Vue user store | `crud/users.py`、鉴权 | 认证业务入口。 |
| `utils/auth.py:9-33` | Authorization 解析与当前用户依赖 | `extract_bearer_token`、`get_current_user` | 所有受保护路由 | `crud.users.get_user_by_token` | 同时兼容 Bearer 和裸 Token；前端使用方式不统一。 |
| `utils/jwt_tokens.py:56-137` | HS256 签名、Claims 生成和校验 | `create_jwt`、`decode_jwt` | `crud/users.py` | `Settings`、HMAC SHA-256 | 必须讲清 `iss/aud/sub/typ/jti/ver/iat/nbf/exp`。 |
| `crud/users.py:64-257` | JWT 签发、验证、轮换、黑名单和版本 | `issue_token_pair`、`get_user_by_token`、`refresh_token_pair` | 用户路由、鉴权依赖 | 用户认证表 | Refresh Token 并发轮换缺少锁或原子条件。 |
| `models/users.py:12-125` | 用户及认证状态表 | `User`、`UserAuthState`、`UserRefreshToken`、`UserTokenBlacklist` | users CRUD、bootstrap | MySQL | 展示 JWT 有状态撤销机制；保留旧 `UserToken`。 |
| `routers/news.py:21-71` | 分类、列表和详情接口 | `get_categories`、`get_news_list`、`get_news_detail` | Vue news store | Redis CRUD、MySQL CRUD | 新闻主业务；详情浏览量与缓存存在不一致。 |
| `config/cache_conf.py:6-53` | Redis 客户端与 JSON 读写 | `redis_client`、`get_json_cache`、`set_cache` | `cache/news_cache.py` | Redis | Redis 异常降级为 miss；配置硬编码、使用 `print`。 |
| `cache/news_cache.py:7-99` | 新闻缓存 key 和 TTL | 各 `get_cached/cache_*` | `crud/news_cache.py` | Redis 封装 | 只有 TTL，没有主动失效。 |
| `crud/news_cache.py:12-125` | Cache-Aside 查询 | `get_categories`、`get_news_list`、`get_news_detail` | 新闻路由 | Redis、MySQL | 解释缓存读穿；缓存浏览量陈旧。 |
| `routers/ai_chat.py:37-269` | 会话、RAG、LLM、SSE 和消息保存 | `ai_chat`、`stream_generator`、`_format_rag_docs` | Vue AIChat | Auth、CRUD、Retriever、LLM | 项目最核心文件；职责集中且断连保存不完整。 |
| `schemas/ai_chat.py:5-25` | AI 请求、消息和会话结构 | `AIChatRequest`、`ChatMessage` | FastAPI 路由 | Pydantic | `stream` 默认 true；请求体 model 不再决定后端模型。 |
| `crud/ai_chat.py:10-154` | 会话、消息和摘要持久化 | `create_chat_session`、`add_chat_message`、`get_recent_chat_messages` | AI 路由、摘要服务 | MySQL | `MAX(message_index)+1` 有同会话并发竞争。 |
| `models/ai_chat.py:12-67` | 会话和聊天消息表 | `UserChatSession`、`ChatMessage` | AI CRUD、bootstrap | MySQL | `message_index` 没有组合唯一约束。 |
| `services/model_factory.py:8-32` | 单例 ChatModel | `get_chat_model` | 重写链、生成链 | DashScope OpenAI 兼容接口 | 主模型没有显式 timeout/max_tokens/temperature。 |
| `services/get_retrievel.py:8-26` | 历史感知检索语句改写 | `get_retrievel_chain` | AI 路由 | Prompt、ChatModel、`StrOutputParser` | 原始问题不会直接检索。文件和函数存在拼写错误但当前可运行。 |
| `services/retriever_factory.py:10-36` | Embedding、Chroma、Retriever 单例 | `get_news_retriever` | AI 路由 | DashScope Embedding、Chroma | 固定 `k=5`，无分数、阈值、过滤或 reranker。 |
| `utils/create_prompt.py:13-127` | 读取 Prompt、提取问题、构建历史 | `extract_latest_user_query`、`build_langchain_summary_history` | AI 路由、重写链 | LangChain Message | 历史和摘要同时进入重写与回答链。 |
| `config/system_prompt.txt:1-9` | Qxia 身份和 RAG 回答规则 | `{RAG_results}` | `ChatPromptTemplate` | Qwen | 仅允许依据检索材料回答。 |
| `config/retrievel_rag_sysytem.txt:1-2` | query rewrite 规则 | `{query}` 由代码挂载 | 重写链 | Qwen | 精确改写结果由模型决定。 |
| `services/RAG_chroma/add_to_chroma.py:52-240` | 离线 RAG 入库 | `_build_embedding_model`、`_build_text_splitter`、`ingest_news_to_chroma` | 命令行 | MySQL、DashScope、Chroma | 代码声明 cosine，但当前持久化集合实际是 L2。 |
| `config/chroma_conf.py:8-51` | Chroma 入库配置 | `IngestConfig`、`load_config_from_env` | 入库、Retriever、检查脚本 | 环境变量 | 类默认 800/120，运行加载默认 500/100，存在双默认。 |
| `services/update_summary.py:33-140` | 超过阈值时更新会话摘要 | `refresh_session_summary_if_needed` | AI 路由 | httpx、Qwen、AI CRUD | 代码已注明应读取所有未摘要消息，当前只取最近十条。 |
| `../frontend/src/views/AIChat.vue:148-321` | AI 请求和 SSE 消费 | `sendMessage`、`fetchAIResponse` | 用户操作 | Fetch、Pinia | 无 AbortController、heartbeat 或显式断连处理。 |
| `../frontend/src/store/modules/chat.js:24-124` | AI 会话和消息状态 | `fetchSessions`、`bindSessionId` | AIChat.vue | Axios、localStorage | 新建会话只重置本地状态。 |
| `../frontend/src/config/api.js:7-17` | 前端 API URL | `apiConfig`、`aiChatConfig` | 前端 stores/views | FastAPI | 后端地址硬编码为本机 8000。 |

## B 级：知道用途即可

| 文件路径 | 文件职责 | 核心接口 | 当前是否需要细看 |
| --- | --- | --- | --- |
| `schemas/base.py`、`schemas/news.py`、`schemas/favorite.py`、`schemas/history.py`、`schemas/users.py` | 业务请求响应和 camelCase 别名 | 各 Pydantic model | 调接口时查阅。 |
| `routers/favorite.py`、`routers/history.py` | 收藏和历史普通业务路由 | check/add/remove/list/clear | 掌握鉴权 CRUD 链即可。 |
| `crud/news.py`、`crud/favorite.py`、`crud/history.py` | 普通 SQLAlchemy 查询、JOIN、分页和删除 | 各 CRUD 函数 | 选择代表函数学习。 |
| `models/news.py`、`models/favorite.py`、`models/history.py` | 新闻业务表、索引、唯一约束和外键 | `News`、`Favorite`、`History` | 理解数据关系。 |
| `services/RAG_chroma/inspect_chroma.py:27-168` | 只读查看集合、数量和样本 | `main` | 会使用即可。 |
| `services/db_bootstrap.py:6-19` | 启动时创建认证和 AI 表 | `ensure_*_tables` | 知道不是完整 migration。 |
| `utils/response.py`、`utils/exception.py`、`utils/exception_handlers.py` | 统一响应和全局异常 | `success_response`、handlers | 注意 DEBUG 堆栈泄露。 |
| `../frontend/src/store/user.js`、`../frontend/src/store/modules/news.js`、`../frontend/src/store/modules/favorite.js`、`../frontend/src/store/modules/history.js` | Pinia 状态和 Axios 请求 | 各 action | 能定位前端请求即可。 |
| `../frontend/src/views/Login.vue`、`../frontend/src/views/Register.vue`、`../frontend/src/views/NewsDetail.vue`、`../frontend/src/views/Favorite.vue`、`../frontend/src/views/History.vue` | 普通业务页面入口 | `onSubmit`、`onMounted` 等 | 面试时说明上游入口。 |

## C 级：当前可以跳过

| 目录或文件 | 原因 |
| --- | --- |
| `frontend/src/style.css` 和各 Vue `<style>` | 与后端和 AI 主链无关。 |
| `frontend/src/i18n/`、静态图片和普通展示组件 | 不影响核心调用链。 |
| `frontend/dist/`、`frontend/node_modules/`、`__pycache__/` | 自动生成或第三方文件。 |
| `.idea/`、`.vscode/`、`.obsidian/` | IDE 和编辑器配置。 |
| `chroma_db/` 内二进制索引 | 运行数据，应通过 Chroma API 检查。 |
| `test_main.http` 当前内容 | 仅测试 `/` 和不存在的 `/hello/User`，不能代表真实测试覆盖。 |

## 当前运行事实

- 【代码已确认】MySQL 当前有 403 条新闻、11 个 AI 会话和 44 条聊天消息。
- 【代码已确认】Redis 当前可连接；检查时没有 `news*` key。
- 【代码已确认】Chroma `news_rag` 有 403 个向量，维度 1024。
- 【代码已确认】当前每篇新闻只有一个 chunk。
- 【代码已确认】当前集合 schema 的向量距离为 L2，不是源码意图中的 cosine。
