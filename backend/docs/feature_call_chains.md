# 主要功能调用链

> 状态说明：本文是 2026-08-07 工程优化前的只读审计快照。AI、RAG、SSE、异常和配置链路的最新变化见 `docs/today_optimization_report.md`。

## 阅读约定

- 【代码已确认】：当前仓库可直接证明。
- 【合理推断】：根据当前安装框架的执行行为推断。
- 【暂时无法确认】：缺少运行环境、外部服务或测试证据。

## 1. 用户注册

### 入口

- 前端：`../frontend/src/views/Register.vue:onSubmit`，L83-L111。
- 前端请求：`../frontend/src/store/user.js:register`，L62-L100。
- 方法和 URL：`POST /api/user/register`。
- 后端：`routers/users.py:register`，L36-L46。

### 完整调用链

```text
Register.vue:onSubmit
→ userStore.register
→ schemas.users.UserRequest
→ routers.users.register
→ crud.users.get_user_by_username
→ crud.users.create_user
→ utils.security.get_hash_password
→ MySQL user
→ crud.users.issue_token_pair
→ utils.jwt_tokens.create_jwt
→ MySQL user_auth_state + user_refresh_token
→ success_response
→ Pinia 保存用户和 Token
```

### 数据流

- 输入：`username`、`password`。
- 中间数据：bcrypt 密码哈希；Access/Refresh JWT Claims。
- 输出：用户信息、Access Token、Refresh Token、过期秒数。
- 写入：`user`、`user_auth_state`、`user_refresh_token`。
- 读取：按 username 查询 `user`。

### 文件清单

| 顺序 | 文件路径与行号 | 类/函数 | 作用 |
| ---: | --- | --- | --- |
| 1 | `../frontend/src/views/Register.vue:83-111` | `onSubmit` | 收集输入并调用 store。 |
| 2 | `../frontend/src/store/user.js:62-100` | `register` | Axios 请求并保存 Token。 |
| 3 | `schemas/users.py:8-10` | `UserRequest` | 请求结构校验。 |
| 4 | `routers/users.py:36-46` | `register` | 注册业务编排。 |
| 5 | `crud/users.py:26-113` | 查询、创建、签发 | 数据库和 JWT 操作。 |

## 2. 用户登录

### 入口

- 前端：`../frontend/src/views/Login.vue:onSubmit`，L67-L100。
- 请求：`POST /api/user/login`。
- 后端：`routers/users.py:login`，L49-L57。

### 完整调用链

```text
Login.vue:onSubmit
→ userStore.login
→ UserRequest
→ routers.users.login
→ crud.users.authenticate_user
→ get_user_by_username
→ security.verify_password
→ issue_token_pair
→ MySQL认证状态
→ 返回Token对和用户信息
```

### 数据流

- 输入：用户名、明文密码。
- 中间数据：数据库密码哈希、JWT Claims。
- 输出：Access/Refresh Token 和用户信息。
- 写入：新增 Refresh Token 状态。
- 读取：`user`、`user_auth_state`。

### 文件清单

| 顺序 | 文件路径与行号 | 类/函数 | 作用 |
| ---: | --- | --- | --- |
| 1 | `../frontend/src/store/user.js:22-60` | `login` | Axios 请求和 Pinia 保存。 |
| 2 | `routers/users.py:49-57` | `login` | 登录入口。 |
| 3 | `crud/users.py:116-123` | `authenticate_user` | 用户和密码验证。 |
| 4 | `crud/users.py:76-113` | `issue_token_pair` | JWT 签发和 Refresh 状态保存。 |

## 3. 新闻分类和新闻列表

### 入口

- 前端：`../frontend/src/views/Home.vue:onMounted`，L78-L83、L166-L174。
- 前端请求：`../frontend/src/store/modules/news.js:getCategories/getNewsList`，L30-L96。
- URL：`GET /api/news/categories`；`GET /api/news/list?categoryId=&page=&pageSize=`。
- 后端：`routers/news.py:get_categories/get_news_list`，L21-L45。

### 完整调用链

```text
Home.vue
→ newsStore.getCategories/getNewsList
→ Axios
→ routers.news
→ crud.news_cache
→ Redis GET
→ [miss] SQLAlchemy SELECT MySQL
→ Redis SETEX
→ get_news_count直接查询MySQL
→ 计算hasMore
→ JSONResponse
```

### 数据流

- 输入：`categoryId`、`page`、`pageSize`。
- 中间数据：`offset=(page-1)*pageSize`、Redis key。
- 输出：分类数组或 `{list,total,hasMore}`。
- 写入：分类和列表 Redis 缓存。
- 读取：Redis、`news_category`、`news`。

【代码已确认】Home 存在两个 `onMounted`，两处都会触发 `getNewsList`，首屏可能重复请求。

## 4. 新闻详情

### 入口

- 前端：`../frontend/src/views/NewsDetail.vue:onMounted`，L135-L153。
- 请求：`GET /api/news/detail?id={newsId}`。
- 后端：`routers/news.py:get_news_detail`，L48-L71。

### 完整调用链

```text
NewsDetail.vue
→ newsStore.getNewsDetail
→ routers.news.get_news_detail
→ crud.news_cache.get_news_detail
→ Redis详情缓存
→ [miss] MySQL news
→ Redis SETEX
→ crud.news.increase_news_views
→ MySQL views + 1
→ crud.news_cache.get_related_news
→ Redis或MySQL
→ 返回详情
```

### 数据流

- 输入：新闻 ID。
- 输出：正文、作者、发布时间、浏览量、相关新闻。
- 写入：详情/相关新闻缓存，MySQL 浏览量。
- 读取：Redis 和 `news`。

【代码已确认】详情在浏览量自增前被读取并缓存，返回值和缓存中的 `views` 可能落后于数据库。

## 5. 新闻收藏

### 入口

- 前端：`../frontend/src/views/NewsDetail.vue:toggleFavorite`，L101-L133。
- Store：`../frontend/src/store/modules/favorite.js`，add L65-L95，remove L98-L124，list L230-L267。
- 后端：`routers/favorite.py`，L15-L72。

### 完整调用链

```text
Vue/Pinia
→ Axios + Authorization
→ utils.auth.get_current_user
→ crud.users.get_user_by_token
→ JWT签名/Claims/黑名单/version校验
→ routers.favorite
→ crud.favorite
→ MySQL favorite [JOIN news]
→ JSONResponse
```

### 数据流

- 输入：`newsId`、分页参数。
- 输出：收藏状态、收藏 ORM 或收藏列表。
- 写入/删除：`favorite`。
- 读取：`favorite JOIN news`。

唯一约束 `(user_id, news_id)` 位于 `models/favorite.py:22-25`。

## 6. 浏览历史

### 入口

- 添加入口：`../frontend/src/views/NewsDetail.vue:onMounted`，L136-L149。
- 列表入口：`../frontend/src/views/History.vue:onMounted`，L126-L142。
- 请求：`POST /api/history/add`、`GET /api/history/list`、`DELETE /api/history/delete/{id}`。
- 后端：`routers/history.py`，L15-L69。

### 完整调用链

```text
新闻详情加载完成
→ historyStore.addHistoryApi(newsId)
→ JWT鉴权
→ crud.history.add_history
→ 查询user_id + news_id
→ 已有则更新view_time / 没有则INSERT
→ MySQL history
```

### 数据流

- 输入：新闻 ID。
- 输出：历史记录或 `history JOIN news` 分页列表。
- 写入：`history`。
- 读取：`history`、`news`。

【代码已确认】删除路由参数名为 `history_id`，CRUD 实际按 `History.news_id` 删除。前端传的是新闻 `item.id`，当前行为可工作，但接口命名不准确。

## 7. AI 创建会话

### 入口

- 前端按钮：`../frontend/src/views/AIChat.vue:createNewSession`，L277-L281。
- Store：`../frontend/src/store/modules/chat.js:resetAsNewSession`，L46-L49。
- 后端没有独立创建会话接口。

### 完整调用链

```text
点击“新建”
→ 仅清空前端currentSessionId和messages
→ 第一次POST /api/ai/chat携带session_id:null
→ routers.ai_chat.ai_chat
→ crud.ai_chat.create_chat_session
→ UUID写入user_chat_session
→ X-Session-Id响应头
→ chatStore.bindSessionId
```

### 数据流

- 输入：当前用户 ID。
- 输出：36 位 UUID。
- 写入：`user_chat_session`。
- 读取：无。

## 8. AI 发送问题

### 入口

- 前端：`../frontend/src/views/AIChat.vue:sendMessage/fetchAIResponse`，L148-L275。
- 请求：`POST /api/ai/chat`。
- 请求 Schema：`schemas/ai_chat.py:AIChatRequest`，L11-L16。
- 后端：`routers/ai_chat.py:ai_chat`，L94-L269。

### 完整调用链

```text
输入问题
→ Fetch POST + Bearer JWT
→ get_current_user + get_db
→ 校验/创建会话
→ extract_latest_user_query
→ 读取摘要和最近10条历史
→ get_retrievel_chain改写query
→ Retriever进行Embedding和Chroma检索
→ _format_rag_docs
→ ChatPromptTemplate
→ qwen3.6-flash
```

### 数据流

- 输入：`messages`、`stream`、`session_id`。
- 中间数据：历史消息、改写 query、5 个 Document、RAG context。
- 输出：SSE 或普通 JSON。
- 写入：模型调用前保存 user 消息。
- 读取：会话、摘要、消息、Chroma。

## 9. AI 流式返回

### 入口

- 后端：`routers/ai_chat.py:stream_generator`，L179-L219。
- 前端：`AIChat.vue:fetchAIResponse`，L218-L261。

### 完整调用链

```text
generation_chain.astream(current_question)
→ AIMessageChunk.content
→ data: JSON\n\n
→ StreamingResponse(text/event-stream)
→ Fetch ReadableStream
→ TextDecoder
→ JSON.parse
→ 拼接更新最后一条assistant消息
```

### 数据流

- 输出帧：`choices[0].delta.content`。
- 终止帧：`data: [DONE]`。
- 写入：完整流结束后保存 assistant 消息。

【合理推断】客户端断连时响应流会被 Starlette 停止或取消；项目没有显式断连处理，因此 assistant 消息通常不会保存，具体时机取决于 ASGI 服务器和断连点。

## 10. AI 聊天记录保存

### 入口

- user 保存：`routers/ai_chat.py:168-176`。
- assistant 保存：流式 L208-L219；非流式 L242-L261。
- CRUD：`crud/ai_chat.py:add_chat_message`，L71-L101。

### 完整调用链

```text
add_chat_message
→ SELECT MAX(message_index)
→ current_max + 1
→ INSERT chat_message
→ commit
→ refresh_session_summary_if_needed
→ [满足阈值] 调用摘要模型
→ UPDATE user_chat_session.summary
```

### 数据流

- 输入：user/session/role/content/model/finish_reason。
- 输出：ChatMessage ORM。
- 写入：`chat_message`、可选 `user_chat_session.summary`。
- 读取：最大消息序号、旧摘要、最近十条消息。

【代码已确认】同会话并发请求可能在 `MAX+1` 处竞争；模型或流失败时已经提交的 user 消息不会被回滚。

## 11. 新闻写入 Chroma

### 入口

```powershell
D:\Anaconda\envs\fastapi311\python.exe services\RAG_chroma\add_to_chroma.py
```

核心函数：`services/RAG_chroma/add_to_chroma.py:ingest_news_to_chroma`，L175-L240。

### 完整调用链

```text
load_config_from_env
→ AsyncSessionLocal
→ _fetch_news_batch
→ _compose_news_text
→ RecursiveCharacterTextSplitter.split_text
→ LangChain Document + metadata
→ Chroma.delete(where=news_id)
→ Chroma.add_documents
→ DashScopeEmbeddings.embed_documents
→ Chroma持久化HNSW索引
```

### 数据流

- 输入：News ORM。
- 中间数据：标题、摘要、作者、正文、chunk、metadata、ID。
- 输出：向量和 Document。
- 写入：`chroma_db`。
- 读取：MySQL `news`。

## 12. Redis 新闻缓存读取与失效

### 入口

- 路由：`routers/news.py:21-59`。
- Cache-Aside：`crud/news_cache.py:12-125`。
- key/TTL：`cache/news_cache.py:7-99`。

### 完整调用链

```text
计算Redis key
→ get_json_cache
→ [hit] 返回缓存
→ [miss/Redis异常] SQLAlchemy查询MySQL
→ Pydantic/JSON编码
→ Redis SETEX
→ 返回路由
```

### 数据流

- key：`news:categories`、`news_list:{category}:{page}:{size}`、`news:detail:{id}`、`news:related:{id}:{category}`。
- 输出：字典或列表。
- 写入：带 TTL 的 Redis key。
- 读取：Redis 和 MySQL。

【代码已确认】仓库没有 Redis `delete`、`unlink` 或业务缓存失效函数，当前只依赖 TTL 自然过期。
