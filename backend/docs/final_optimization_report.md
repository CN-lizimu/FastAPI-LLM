# 收尾型工程优化报告

优化日期：2026-08-07

本轮只完善现有 Dense RAG、日志、OOD拒绝和Redis一致性，不新增Agent、GraphRAG、Hybrid Retrieval、消息队列或数据库迁移。

## 1. 本次修改文件

| 文件 | 修改内容 | 影响 |
| --- | --- | --- |
| `config/settings.py` | 增加日志级别、RAG调试开关、旧/候选集合名；在线集合切换为cosine；阈值改为0.5 | 配置、RAG |
| `.env.example` | 补齐对应环境变量且不包含真实Secret | 启动配置 |
| `utils/observability.py` | 显式设置root日志级别，增加安全文本截断 | 全局日志 |
| `main.py` | 使用 `APP_LOG_LEVEL` 初始化日志 | 应用启动 |
| `services/retriever_factory.py` | 读取Collection实际metric，记录检索事件、候选摘要、阈值过滤和调试chunk | 在线RAG |
| `routers/ai_chat.py` | 增加请求、改写、生成和完成事件；无资料时确定性兜底 | AI聊天/SSE |
| `services/RAG_chroma/add_to_chroma.py` | 重建时只删除目标Collection，不再删除整个持久化目录 | 离线入库安全 |
| `evaluation/build_cosine_candidate.py` | 安全复制旧集合为独立cosine候选集合 | 距离度量实验 |
| `evaluation/compare_chroma_metrics.py` | 比较Recall、score分布、延迟、阈值和OOD拒绝率 | RAG评测 |
| `evaluation/rag_broad_cases.json` | 6条宽泛新闻问题 | 阈值边界验证 |
| `evaluation/rag_ood_cases.json` | 20条严格领域外问题 | OOD评测 |
| `config/cache_conf.py` | 增加精确key删除和基于SCAN的pattern删除 | Redis |
| `cache/news_cache.py` | 浏览量变化后的相关缓存失效 | 新闻缓存 |
| `crud/news.py`、`routers/news.py` | 数据库更新成功后触发失效 | 新闻详情 |
| `tests/test_rag_observability.py` | metric、日志、score转换和缓存失效测试 | 回归测试 |
| `README.md`、`evaluation/README.md` | 更新运行、集合、阈值和评测说明 | 项目文档 |

`database.sql`、SQLAlchemy Model、前端源码和依赖均未修改。

## 2. 日志系统修改前后

### 修改前

【代码已确认】原来的 `print` 已被删除；AI日志不是因DEBUG、handler或propagate问题而不可见。实际缺口是业务事件不足：只有简化的 `rag_retrieval_completed` 和尾部完成事件，没有请求、改写、检索开始、候选详情、无结果和模型开始事件。

`configure_logging` 原来只读取 `DEBUG`，且 `logging.basicConfig` 在Uvicorn已安装handler时可能不改变root level。

### 修改后

默认 `APP_LOG_LEVEL=INFO`，一次正常请求可按同一 `trace_id` 观察：

```text
ai_chat_request_received
rag_query_rewrite_completed
rag_retrieval_started
rag_retrieval_completed
llm_generation_started
llm_generation_completed
ai_chat_completed
http_request_completed
```

无资料时额外出现 `rag_no_relevant_documents`。

INFO只记录query、ID、标题、chunk_index、score、来源、配置和耗时，不记录新闻正文、JWT、API Key或密码。只有 `RAG_DEBUG_LOG=true` 才输出 `rag_debug_chunks` 和 `rag_debug_context`，正文、RAG结果及Prompt摘要分别受 `RAG_DEBUG_MAX_CHARS=800` 截断。

## 3. 一次真实AI请求的日志示例

正常问题：`中国科技成就有哪些？`，trace_id为 `final-normal-rag-trace`。

```json
{"event":"ai_chat_request_received","trace_id":"final-normal-rag-trace","query":"中国科技成就有哪些？","query_length":10,"stream":true}
{"event":"rag_query_rewrite_completed","trace_id":"final-normal-rag-trace","rewritten_query":"中国重大科技成就","duration_ms":5339.08}
{"event":"rag_retrieval_started","trace_id":"final-normal-rag-trace","top_k":5,"score_threshold":0.5,"collection":"news_rag_cosine_candidate","distance_metric":"cosine"}
{"event":"rag_retrieval_completed","trace_id":"final-normal-rag-trace","retrieved_count":5,"documents":[{"news_id":300,"title":"量子计算机突破性进展","chunk_index":0,"relevance_score":0.6234}]}
{"event":"llm_generation_started","trace_id":"final-normal-rag-trace","model":"qwen3.6-flash","stream":true,"contextual_document_count":5}
{"event":"llm_generation_completed","trace_id":"final-normal-rag-trace","generation_duration_ms":12811.11,"output_character_count":504,"success":true,"sse_chunk_count":81}
{"event":"ai_chat_completed","trace_id":"final-normal-rag-trace","rewrite_ms":5339.08,"retrieval_ms":3041.65,"llm_ms":12811.11,"total_ms":21379.66,"retrieved_document_count":5}
```

SSE真实返回5个sources、81个内容块、504个字符和 `[DONE]`，没有错误帧。

## 4. L2与cosine实际测试结果

### Collection状态

| 项目 | 旧集合 | cosine候选/当前在线集合 |
| --- | --- | --- |
| 名称 | `news_rag` | `news_rag_cosine_candidate` |
| persist目录 | `backend/chroma_db` | `backend/chroma_db` |
| metadata | `null` | `hnsw:space=cosine`、源集合和构建方式 |
| configuration实际metric | `l2` | `cosine` |
| 文档数 | 403 | 403 |

旧集合没有删除或覆盖。候选集合直接复制旧集合已有的403条Document、metadata和embedding，因此两者文档内容、切块边界和向量完全相同，实验只改变HNSW distance metric。

【暂时无法确认】旧Collection metadata没有记录历史 `chunk_size` 和 `chunk_overlap`，因此无法仅从持久化集合证明其最初数值。直接复制现有chunk比按当前500/100重新切分更能隔离距离度量变量；未来重新从MySQL入库时使用当前500/100配置。

### 指标

| 指标 | L2 | cosine |
| --- | ---: | ---: |
| Recall@1 | 1.0000 | 1.0000 |
| Recall@3 | 1.0000 | 1.0000 |
| Recall@5 | 1.0000 | 1.0000 |
| 正常问题平均最高score | 0.7360 | 0.8133 |
| Chroma查询平均耗时 | 12.63 ms | 8.76 ms |
| Embedding+Chroma平均耗时 | 307.40 ms | 303.53 ms |

延迟只运行了一轮且主要受网络Embedding耗时影响，不能据此宣称cosine存在稳定性能优势。

## 5. 正常问题与OOD分数分布

| 集合/问题集 | min | mean | median | max |
| --- | ---: | ---: | ---: | ---: |
| L2精确正常问题 | 0.6657 | 0.7360 | 0.7340 | 0.8012 |
| L2宽泛新闻问题 | 0.3516 | 0.4738 | 0.4912 | 0.5460 |
| L2 OOD | 0.0774 | 0.1737 | 0.1731 | 0.2609 |
| cosine精确正常问题 | 0.7636 | 0.8133 | 0.8119 | 0.8594 |
| cosine宽泛新闻问题 | 0.5415 | 0.6279 | 0.6402 | 0.6790 |
| cosine OOD | 0.3477 | 0.4157 | 0.4153 | 0.4774 |

这里的score由当前 `langchain-community` Chroma规则转换：cosine为 `1-distance`；L2为 `1-distance/sqrt(2)`。不同metric的score绝对值不能直接共用同一经验阈值。

## 6. threshold选择依据

cosine候选集合：

| threshold | Recall@1/3/5 | OOD rejection accuracy | 宽泛问题接受率 |
| ---: | ---: | ---: | ---: |
| 0.2 | 1.0 / 1.0 / 1.0 | 0.00 | 1.00 |
| 0.3 | 1.0 / 1.0 / 1.0 | 0.00 | 1.00 |
| 0.4 | 1.0 / 1.0 / 1.0 | 0.25 | 1.00 |
| 0.5 | 1.0 / 1.0 / 1.0 | 1.00 | 1.00 |

因此当前默认值调整为 `RAG_SCORE_THRESHOLD=0.5`，并将在线集合切换为cosine候选集合。这个选择基于10条精确问题、6条宽泛问题和20条OOD问题，只是当前小型基线，不是长期固定结论。Embedding模型、新闻数据或切分变化后必须重跑。

## 7. Recall@K

10条带目标新闻ID的问题在两个集合中的未过滤 Recall@1、Recall@3、Recall@5均为1.0。cosine在0.5过滤后仍均为1.0。

`evaluate_rag.py` 使用当前在线cosine集合和0.5阈值再次运行，结果仍为 `case_count=10, recall_at_k=1.0`。该结果不能代表答案事实正确率，也不能代表开放问题整体准确率。

## 8. OOD rejection accuracy

`rag_ood_cases.json` 包含20条电子游戏攻略、食谱、编程语法、数学证明、医疗诊断、维修和手工操作等领域外问题。

cosine在0.5下的离线OOD拒绝率为 `20/20 = 1.0`。真实在线测试还覆盖了Query Rewrite：

```text
原问题：Rust编译器报E0502借用冲突时，应该如何调整可变借用的作用域？
改写：Rust E0502 借用冲突 调整可变借用作用域
最高score：0.4475
过滤后文档数：0
sources：[]
回答：根据当前新闻数据无法确定
```

无文档时不再让回答模型自由生成，而是确定性返回固定话术。SSE仍保持原格式，发送1个content块和 `[DONE]`；日志中的 `skipped_due_to_no_context=true` 明确表示跳过回答模型。

## 9. Redis缓存修改

项目没有新闻新增、编辑或删除接口。本轮只处理现有浏览量写操作：

```text
UPDATE news.views并commit
→ 删除 news:detail:{news_id}
→ SCAN并删除 news_list:{category_id}:*
→ SCAN并删除 news:related:*:{category_id}
→ 下一次读取从MySQL回填
```

分类缓存不依赖浏览量，不删除；收藏和历史不改变新闻响应主体，也不删除新闻缓存。Redis删除失败会记录warning，但不会回滚已经成功的MySQL浏览量更新。

真实Redis测试删除3个相关测试key，并保留另一分类的测试key。

## 10. sources与前端

后端继续在SSE首帧和非流式响应中提供sources，字段包括新闻ID、标题、分类、发布时间、来源和score。本轮没有修改前端；旧前端仍能忽略不认识的sources帧并继续读取 `choices[0].delta.content`。

当前前端尚未可视化来源，这属于后续可选展示优化，不影响接口兼容。

## 11. 实际测试结果

### 已实际通过

- `python -m compileall -q .`
- `pytest -q`：11项通过
- FastAPI在 `127.0.0.1:8001` 启动
- `/health`：200，Backend/Database/Redis均为ok
- MySQL注册、JWT鉴权、聊天消息保存与临时数据清理
- Redis主动失效：目标key删除且其他分类key保留
- 旧L2集合：403条、实际metric为l2
- cosine候选：403条、实际metric为cosine
- 正常RAG、Query Rewrite、Embedding、Chroma、Qwen、SSE和sources
- OOD在线改写、阈值拒绝、固定兜底、SSE和空sources
- L2/cosine/阈值/OOD评测脚本

### 未做完整验证

- 20条OOD是小规模人工样例，没有覆盖所有领域外表达。
- 没有做多轮统计显著性或并发性能基准。
- 没有做浏览器真实断连和反向代理SSE压测。
- `RAG_DEBUG_LOG=true` 已有单元级截断验证，本轮真实API验收使用安全默认值false，避免把正文写入验收日志。

## 12. 尚未解决的问题

### 高

1. 评测集规模仍小，阈值0.5必须随新闻库、Embedding或chunk变化持续回归。
2. `database.sql` 与启动时 `create_all` 的JWT/聊天表仍缺少正式迁移体系，本轮未改数据库。

### 中

1. Query Rewrite仍调用一次LLM；即使最终是OOD，也会产生改写耗时和调用成本。
2. 第一次检索会包含Chroma和Embedding对象初始化时间；业务日志中的外层 `retrieval_ms` 高于纯查询事件耗时。
3. SSE的 `http_request_completed` 在StreamingResponse建立后记录，而真正流结束应看 `ai_chat_completed`。
4. 浏览量每次变化都会使同分类列表和相关新闻缓存失效，正确性提高但缓存命中率会下降，需要后续观察。
5. Semaphore仍是单进程限制，多worker不共享计数。

### 低

1. LangChain community版Chroma和ChatOpenAI仍有弃用警告；遵守本轮不升级依赖要求未迁移。
2. 前端暂未展示sources。
3. Windows把stderr重定向到文件时使用系统代码页；PowerShell读取该文件应使用默认编码，正常交互终端可直接显示中文。

## 13. 面试中最值得讲的5个改动

1. **从“有日志”升级为可追踪业务事件。** 通过ContextVar trace_id串联改写、检索、生成和SSE，并控制INFO与调试正文的边界。
2. **用受控实验解决distance不一致。** 不删除旧L2集合，复制同一批文档和向量建立cosine集合，只改变一个实验变量。
3. **用正常、宽泛和OOD三类问题选择阈值。** 不只追求Recall，也验证领域外拒绝率和宽泛问题误拒绝率。
4. **无资料时确定性降级。** Retriever过滤为空后不让回答模型自由发挥，固定返回规则话术，同时保持SSE协议兼容。
5. **数据库写成功后主动失效缓存。** 使用精确删除与Redis SCAN删除可能陈旧的key，说明Cache-Aside的一致性边界和失败降级策略。
