# RAG V2 评测报告

评测日期：2026-08-08

## 1. 结论

本轮在同一 cosine Chroma Collection、同一 Embedding 模型和同一评测集上比较了 Dense、Hybrid、Hybrid + Rerank 三条检索流水线。当前 14 条正常问题中，三条流水线的目标新闻均排在第 1 位；20 条 OOD 问题在阈值 0.5 下均被拒绝。因此，本数据集没有证明 Hybrid 或 Reranker 比 Dense 更准确。

默认配置继续采用 **Dense + threshold 0.5 + rerank disabled**。原因是它取得相同质量指标，平均延迟最低，且没有额外 rerank API 可用性与费用风险。Hybrid 和 Hybrid + Rerank 已实现为可切换实验模式，待扩大困难样本后重新判断。

## 2. 实验条件

| 项目 | 实际值 |
| --- | --- |
| Collection | `news_rag_cosine_candidate` |
| Collection 文档块数 | 403 |
| 距离度量 | cosine |
| Embedding | `text-embedding-v3` |
| Dense candidate K | 20 |
| BM25 candidate K | 20 |
| RRF K | 60 |
| Rerank candidate K | 10 |
| Rerank model | `gte-rerank-v2` |
| Final Top-K | 5 |
| 默认 threshold | 0.5 |
| 正常问题 | 14 |
| OOD 问题 | 20 |

三个模式使用完全相同的数据集。每个模式先执行一次 warm-up，warm-up 不计入延迟指标。正常集每条只有一个 `expected_document_id`，所以本次 Hit@K 与 Recall@K 数值相同。

## 3. 核心结果

| Pipeline | Recall@1 | Recall@3 | Recall@5 | MRR | OOD Reject | Avg Latency | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Dense | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 290.22 ms | 410.65 ms |
| Hybrid | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 328.83 ms | 461.96 ms |
| Hybrid + Rerank | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 521.19 ms | 712.26 ms |

Hit@1、Hit@3、Hit@5 在三个模式下也均为 1.0000。34 次 rerank 调用（包含正常集和 OOD 集）没有失败，但正常问题的目标新闻在 rerank 前后均为第 1 位，没有产生可测的名次收益。

相对 Dense，Hybrid 平均增加 38.61 ms；Hybrid + Rerank 平均增加 230.97 ms。复杂方案在当前样本上只增加了延迟。

## 4. 阶段耗时

以下为 14 条正常问题的阶段平均值：

| Pipeline | Init | Dense | BM25 | Fusion | Rerank | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dense | 3.09 ms | 286.72 ms | 0 | 0 | 0 | 290.22 ms |
| Hybrid | 3.03 ms | 325.17 ms | 4.46 ms | 0.18 ms | 0 | 328.84 ms |
| Hybrid + Rerank | 2.98 ms | 280.01 ms | 3.63 ms | 0.17 ms | 237.50 ms | 521.19 ms |

`dense_ms` 包含 DashScope 查询 Embedding 和 Chroma 检索。当前 LangChain `asimilarity_search_with_relevance_scores` 在一个调用内部完成这两步，项目无法在不绕过该封装的情况下准确拆分，报告没有伪造独立的 embedding 时间。

## 5. Threshold 实验

Threshold 只作用于 **最高 Dense cosine relevance score**。BM25 score、RRF score 和 rerank score 的尺度不同，均不与 threshold 比较。

三个 pipeline 在各阈值下结果相同：

| Threshold | 正常 Recall@1 | 正常 Recall@3 | 正常 Recall@5 | OOD rejection |
| ---: | ---: | ---: | ---: | ---: |
| 0.3 | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| 0.4 | 1.0000 | 1.0000 | 1.0000 | 0.2500 |
| 0.5 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| 0.6 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

最高 Dense score 分布：

| 数据集 | Min | Mean | Median | Max |
| --- | ---: | ---: | ---: | ---: |
| 正常问题 | 0.7636 | 0.8060 | 0.8075 | 0.8594 |
| OOD 问题 | 0.3477 | 0.4157 | 0.4153 | 0.4774 |

当前样本在 0.4774 与 0.7636 之间存在明显间隔，因此保留 0.5。这个结论只适用于当前 Collection 和 34 条样本；扩充困难问题或更换 Embedding/Collection 后必须重跑，不能把 0.5 当作通用常数。

## 6. Query Rewrite 验证

修改前，所有问题都会调用 Rewrite LLM，提示词也没有禁止新增限制，真实问题“中国在经济方面有什么新闻”可能被改成“中国近期经济新闻与发展动态”。

修改后：

- 独立问题直接使用原 query，不调用 Rewrite LLM。
- 只有存在历史消息且 query 含“它”“这个”“刚才”“上述”等明确上下文指代时才调用。
- Prompt 明确禁止新增原问题和历史中都不存在的时间、地域、人物、事件或立场。
- 单测覆盖独立问题、无历史指代问题和有历史指代问题。

真实多轮验证中，“它向哪些用户开放？”正确召回新闻 ID 314，接口总耗时约 7.15 秒。Rewrite 仍是一次远程 LLM 调用，依赖网络和模型响应；独立问题现在消除了这段开销。

## 7. 服务级验证

实际启动地址：`http://127.0.0.1:8011`。

| 检查项 | 实际结果 |
| --- | --- |
| FastAPI import | 通过，应用名 `FastAPI LLM News API` |
| `/health` | HTTP 200；backend/database/redis 均为 `ok` |
| JWT 注册与登录 | 通过 |
| 正常 RAG + SSE | HTTP 200；15 个 SSE event；含 `sources` 和 `[DONE]` |
| Sources | 正常问题返回来源；包含 Dense/BM25/RRF/Rerank/final rank 可选字段 |
| OOD/no-answer | 固定无资料回答；`sources` 数量为 0 |
| 多轮上下文 | 指代问题成功 rewrite，Top source 为新闻 ID 314 |
| pytest | 18 passed |

一次真实 OOD 请求的事件链共享同一个 `trace_id`：

```text
ai_chat_request_received
rag_query_rewrite_completed
rag_retrieval_started
rag_dense_completed
rag_retrieval_completed
rag_no_relevant_documents
llm_generation_started
llm_generation_completed
ai_chat_completed
http_request_completed
```

Hybrid 模式会在其中额外产生 `rag_bm25_completed`、`rag_fusion_completed`；启用精排后还会产生 `rag_rerank_started`、`rag_rerank_completed`。INFO 日志只记录 ID、标题、rank、score、数量与耗时，不记录完整正文或密钥。

## 8. 修改文件

| 文件 | 作用 |
| --- | --- |
| `config/settings.py` | V2 模式、候选数、RRF、BM25、rerank 配置 |
| `.env.example` | V2 环境变量模板 |
| `services/bm25_retriever.py` | 中文混合文本分词和 Okapi BM25 |
| `services/reranker.py` | DashScope TextReRank 异步超时包装 |
| `services/retriever_factory.py` | Dense/Hybrid/RRF/rerank 编排、阈值、日志、耗时 |
| `services/get_retrievel.py` | 上下文依赖判定 |
| `config/retrievel_rag_sysytem.txt` | Rewrite 事实约束 |
| `routers/ai_chat.py` | 使用结构化检索结果、阶段耗时和扩展 sources |
| `evaluation/evaluate_rag.py` | 三模式、Recall/Hit/MRR/OOD/延迟/threshold 统一评测 |
| `evaluation/rag_cases.json` | 增加 4 条专有名词问题 |
| `tests/test_rag_v2.py` | BM25、RRF、rewrite、rerank fallback 单测 |

## 9. 如何复现

在 `backend` 目录、`D:/Anaconda/envs/fastapi311` 环境中运行：

```powershell
D:\Anaconda\envs\fastapi311\python.exe -m pytest -q
D:\Anaconda\envs\fastapi311\python.exe -m evaluation.evaluate_rag
```

第二条命令需要可用的 DashScope API Key、cosine Collection 和网络，会生成已被 Git 忽略的 `evaluation/rag_v2_results.json`。

## 10. 尚未解决的问题

1. 正常集只有 14 条且每题只有一个相关新闻 ID，难以衡量多相关文档场景的 Precision、nDCG 和 chunk 级标注质量。
2. 当前简单样本 Dense 已全部 Top-1，存在天花板效应，无法证明 Hybrid/Rerank 在困难同义表达、缩写或实体歧义上的价值。
3. BM25 索引从 Chroma 快照加载并进程内缓存。若运行时重建 Collection，需调用 `clear_retrieval_caches()` 或重启进程；项目目前没有在线新闻写入 Collection 的 API。
4. BM25 为内存索引，适合当前 403 个 chunk；数据量显著增大后需要评估启动时间和内存，而不是直接引入更重基础设施。
5. Reranker 失败已回退 RRF，但当前没有熔断或独立限流；默认关闭规避了该风险。
6. LangChain `Chroma` 和 `ChatOpenAI` 仍有 deprecation warning。本轮遵守“不大规模升级依赖”的约束，记录为 Future Work。
7. 本轮没有评测答案忠实度、引用正确率和生成质量，指标仅覆盖检索与 OOD gate。
