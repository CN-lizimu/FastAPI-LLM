# 核心参数清单

> 状态说明：本文是 2026-08-07 工程优化前的参数审计快照。当前生效值以 `config/settings.py`、`.env.example` 和 `docs/today_optimization_report.md` 为准。

## 使用说明

“当前设置依据”只使用以下分类：

- 代码注释明确说明
- README明确说明
- 可以从业务逻辑推断
- 经验值，代码中没有依据
- 暂时无法确认

【代码已确认】当前运行配置通过 `D:\Anaconda\envs\fastapi311` 中的实际对象只读核对。没有调用远程 LLM 或 Embedding API。

## RAG、Chroma 与模型

| 参数                   |                               当前值 | 文件及行号                                     | 单位    | 实际作用                      | 值过大影响            | 值过小影响           | 当前设置依据      |
| -------------------- | --------------------------------: | ----------------------------------------- | ----- | ------------------------- | ---------------- | --------------- | ----------- |
| `chunk_size`         |                               500 | `config/chroma_conf.py:42`                | 字符    | 单个 chunk 最大长度             | 多主题混杂、context 变长 | 语义被切碎           | 经验值，代码中没有依据 |
| `chunk_overlap`      |                               100 | `config/chroma_conf.py:43`                | 字符    | 相邻块保留上下文                  | 重复向量和 token 增多   | 边界信息丢失          | 经验值，代码中没有依据 |
| dataclass 默认         |                           800/120 | `config/chroma_conf.py:12-13`             | 字符    | 直接构造 `IngestConfig()` 时使用 | 与运行 fallback 漂移  | 与运行 fallback 漂移 | 暂时无法确认      |
| `separators`         | `\n\n, \n, 。, ！, ？, ；, ，, 空格, 空串` | `services/RAG_chroma/add_to_chroma.py:89` | 分隔规则  | 优先按中文语义边界切分               | 规则过细会产生碎片        | 更容易硬切断语义        | 可以从业务逻辑推断   |
| `length_function`    |                      Python `len` | Splitter 未显式设置                            | 字符    | 计算 chunk 长度               | 不能直接对应 token 预算  | 同左              | 暂时无法确认      |
| `k`                  |                                 5 | `services/retriever_factory.py:36`        | chunk | 返回最相关的五个块                 | 噪声和 Prompt 增长    | 召回不足            | 代码注释明确说明    |
| `search_type`        |                      `similarity` | `as_retriever` 未显式传入                      | 模式    | 普通最近邻检索                   | 不适用              | 不适用             | 暂时无法确认      |
| `score_threshold`    |                               未设置 | `services/retriever_factory.py:36`        | 分数    | 当前不淘汰低相关结果                | 高阈值可能漏召回         | 低阈值引入噪声         | 暂时无法确认      |
| metadata filter      |                               未设置 | `services/retriever_factory.py:36`        | 条件    | 当前不按分类/时间过滤               | 过严会漏数据           | 无过滤会混入其他主题      | 暂时无法确认      |
| MMR/reranker         |                               未使用 | Retriever 全文件                             | 策略    | 当前只按单一距离排序                | 参数复杂、额外延迟        | 结果可能重复或单一       | 暂时无法确认      |
| 源码声明距离               |                            cosine | `services/RAG_chroma/add_to_chroma.py:77` | 度量    | 新建集合时意图使用余弦距离             | 不适用              | 不适用             | 代码注释明确说明    |
| 当前实际距离               |                        L2 squared | Chroma 持久化 schema                         | 距离    | 当前 HNSW 排序依据              | 不适用              | 数值越小越相似         | 暂时无法确认      |
| collection           |                        `news_rag` | `config/chroma_conf.py:40`                | 名称    | Chroma 集合                 | 不适用              | 不适用             | 可以从业务逻辑推断   |
| persist dir          |               `backend/chroma_db` | `config/chroma_conf.py:41`                | 路径    | Chroma 持久化目录              | 不适用              | 不适用             | 可以从业务逻辑推断   |
| Embedding 类          |             `DashScopeEmbeddings` | `services/retriever_factory.py:23-26`     | 类     | 文本转向量                     | 不适用              | 不适用             | 可以从业务逻辑推断   |
| Embedding 模型         |               `text-embedding-v3` | `config/settings.py:18`                   | 模型    | 文档/query 语义向量             | 不适用              | 不适用             | 暂时无法确认      |
| 当前向量维度               |                              1024 | Chroma 只读运行检查                             | 维     | 向量坐标数量                    | 存储和计算增加          | 可能损失表达能力        | 暂时无法确认      |
| Chat 类               |    `ChatOpenAI` community adapter | `services/model_factory.py:3,27`          | 类     | 调用 OpenAI 兼容接口            | 不适用              | 不适用             | 可以从业务逻辑推断   |
| Chat 模型              |                   `qwen3.6-flash` | `.env`、`config/settings.py:15`            | 模型    | query rewrite 和回答         | 成本/延迟取决于模型       | 能力可能不足          | 暂时无法确认      |
| `temperature`        |                               0.7 | 项目未设置，当前依赖默认                              | 0-2   | 控制输出随机性                   | RAG 回答和改写不稳定     | 输出更确定           | 经验值，代码中没有依据 |
| `max_tokens`         |                            `None` | `services/model_factory.py:27-32`         | token | 项目不限制输出长度                 | 成本和输出长度增加        | 容易截断            | 暂时无法确认      |
| 主 LLM timeout        |                            `None` | `services/model_factory.py:27-32`         | 秒     | 项目没有显式请求超时                | 请求长期占用资源         | 正常慢请求易失败        | 暂时无法确认      |
| 主 LLM retries        |                                 2 | 当前 `ChatOpenAI` 依赖默认                      | 次     | 上游失败重试                    | 总等待增加            | 临时错误难恢复         | 经验值，代码中没有依据 |
| `streaming`          |                              true | `services/model_factory.py:31`            | 布尔    | 允许逐 chunk 输出              | 不适用              | 无法 SSE 流式输出     | 可以从业务逻辑推断   |
| 入库 Embedding retries |                                 3 | `services/RAG_chroma/add_to_chroma.py:60` | 次     | 文档 Embedding 重试           | 失败等待增加           | 临时错误难恢复         | 代码注释明确说明    |
| DB fetch batch       |                                 5 | `config/chroma_conf.py:44`                | 新闻    | 每次 MySQL 读取数量             | 内存/处理批次增大        | SQL 次数增加        | 代码注释明确说明    |
| Chroma write batch   |                                 5 | `config/chroma_conf.py:45`                | chunk | 每次写入数量                    | API/内存压力增加       | 入库耗时增加          | 经验值，代码中没有依据 |
| Chroma add retries   |                                 3 | `config/chroma_conf.py:47`                | 次     | 写入失败重试                    | 最终失败耗时增加         | 稳定性降低           | 代码注释明确说明    |
| retry delay          |                               2.0 | `config/chroma_conf.py:48`                | 秒基数   | 线性回退等待                    | 入库失败等待长          | 容易快速重复失败        | 经验值，代码中没有依据 |

## Redis 与新闻接口

| 参数 | 当前值 | 文件及行号 | 单位 | 实际作用 | 值过大影响 | 值过小影响 | 当前设置依据 |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| Redis host | `localhost` | `config/cache_conf.py:6` | 地址 | Redis 服务地址 | 不适用 | 不适用 | 暂时无法确认 |
| Redis port | 6379 | `config/cache_conf.py:7` | 端口 | Redis 连接端口 | 不适用 | 不适用 | 暂时无法确认 |
| Redis DB | 0 | `config/cache_conf.py:8` | 编号 | 逻辑数据库 | 易与其他应用共享 key | 不适用 | 暂时无法确认 |
| Redis 默认 TTL | 3600 | `config/cache_conf.py:44` | 秒 | 未传 expire 时使用 | 数据陈旧 | 命中率降低 | 经验值，代码中没有依据 |
| 分类 TTL | 7200 | `cache/news_cache.py:21` | 秒 | 分类缓存 | 分类更新延迟 | DB 压力增加 | 代码注释明确说明 |
| 列表 TTL | 1800 | `cache/news_cache.py:26` | 秒 | 新闻列表缓存 | 列表/浏览量陈旧 | DB 压力增加 | 代码注释明确说明 |
| 详情 TTL | 300 | `cache/news_cache.py:54` | 秒 | 新闻详情缓存 | 浏览量和内容陈旧 | DB 压力增加 | 代码注释明确说明 |
| 相关新闻 TTL | 1800 | `cache/news_cache.py:70` | 秒 | 相关新闻缓存 | 推荐结果陈旧 | DB 压力增加 | 可以从业务逻辑推断 |
| 分类 limit | 100 | `routers/news.py:22` | 条 | 分类最大读取量 | 响应变大 | 分类被截断 | 经验值，代码中没有依据 |
| 新闻 page size | 10 | `routers/news.py:32` | 条 | 默认列表分页 | 响应变大 | 请求次数增加 | 经验值，代码中没有依据 |
| 新闻 page size max | 100 | `routers/news.py:32` | 条 | 单次最大列表量 | 响应和 DB 压力 | 限制客户端批量能力 | 可以从业务逻辑推断 |
| 相关新闻 limit | 5 | `crud/news_cache.py:98` | 条 | 详情页推荐数量 | 页面/查询数据多 | 推荐不足 | 经验值，代码中没有依据 |

## MySQL、JWT、会话与 SSE

| 参数 | 当前值 | 文件及行号 | 单位 | 实际作用 | 值过大影响 | 值过小影响 | 当前设置依据 |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| `pool_size` | 10 | `config/db_conf.py:24` | 连接 | 常驻连接池连接 | MySQL 压力增加 | 并发请求排队 | 代码注释明确说明 |
| `max_overflow` | 20 | `config/db_conf.py:25` | 连接 | 高峰临时连接 | MySQL 过载 | 高峰等待 | 代码注释明确说明 |
| `expire_on_commit` | false | `config/db_conf.py:33` | 布尔 | commit 后 ORM 属性仍可读 | 对象可能保存旧状态 | 访问属性可能重新加载 | 可以从业务逻辑推断 |
| SQL echo | true | `config/db_conf.py:23` | 布尔 | 输出所有 SQL | 日志噪声和信息泄露 | 排障信息减少 | 代码注释明确说明 |
| JWT algorithm | HS256 | `config/settings.py:20` | 算法 | HMAC 签名 | 不适用 | 不适用 | 可以从业务逻辑推断 |
| Access Token | 30 | `config/settings.py:23` | 分钟 | Access JWT 有效期 | 泄露窗口变大 | 频繁刷新 | 经验值，代码中没有依据 |
| Refresh Token | 14 | `config/settings.py:24` | 天 | Refresh JWT 有效期 | 撤销表增长、泄露窗口 | 频繁登录 | 经验值，代码中没有依据 |
| 最近会话历史 | 10 | `routers/ai_chat.py:131` | 消息 | 短期上下文 | Prompt 污染和成本 | 指代信息丢失 | 经验值，代码中没有依据 |
| 会话列表 limit | 100 | `routers/ai_chat.py:42` | 会话 | 一次返回会话数 | 响应变大 | 老会话不可见 | 经验值，代码中没有依据 |
| 摘要首次条件 | `current_index > 10` | `services/update_summary.py:43-45` | 消息序号 | 前十条不生成摘要 | 长上下文保留更久 | 过早摘要损失细节 | 代码注释明确说明 |
| 摘要增量条件 | `current-last > 10` | `services/update_summary.py:46-47` | 消息序号 | 每新增超过十条再摘要 | 摘要滞后 | 模型调用频繁 | 代码注释明确说明 |
| 摘要读取消息 | 10 | `services/update_summary.py:51-52` | 消息 | 当前摘要输入窗口 | Prompt 增长 | 可能遗漏未摘要消息 | 代码注释明确说明 |
| 摘要 HTTP timeout | 60 | `services/update_summary.py:86` | 秒 | 摘要请求超时 | Session 占用时间长 | 慢请求易失败 | 经验值，代码中没有依据 |
| `AIChatRequest.stream` | true | `schemas/ai_chat.py:15` | 布尔 | 默认走 SSE | 不适用 | 默认变为普通 JSON | 可以从业务逻辑推断 |
| SSE MIME | `text/event-stream` | `routers/ai_chat.py:223` | MIME | 浏览器识别流式事件 | 不适用 | 客户端可能不按 SSE 处理 | 可以从业务逻辑推断 |
| SSE 帧分隔 | `\n\n` | `routers/ai_chat.py:194` | 字符 | 划分 SSE event | 不适用 | 客户端无法正确拆帧 | 可以从业务逻辑推断 |
| SSE 结束标记 | `[DONE]` | `routers/ai_chat.py:203,206` | 文本 | 通知客户端模型流结束 | 不适用 | 客户端不知逻辑终点 | 可以从业务逻辑推断 |
| SSE heartbeat | 未设置 | `stream_generator` | 秒 | 当前没有保活帧 | 额外流量 | 代理空闲超时 | 暂时无法确认 |
| SSE retry/id/event | 未设置 | `stream_generator` | 字段 | 当前仅发送 data | 协议更复杂 | 不支持断点/事件类型 | 暂时无法确认 |

## 已确认的配置漂移

1. 【代码已确认】`IngestConfig` 类默认 800/120，主流程 fallback 500/100。
2. 【代码已确认】入库源码声明 cosine，当前持久化 Chroma schema 实际为 L2。
3. 【代码已确认】Redis TTL 注释曾描述列表 600、详情 1800，但实际代码是列表 1800、详情 300，应以函数默认参数为准。
4. 【代码已确认】主 ChatModel 的 timeout、temperature、max_tokens 没有进入 Settings。
5. 【代码已确认】DB、Redis 和 CORS 配置没有统一进入 `Settings`。
