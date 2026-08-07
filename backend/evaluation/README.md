# RAG 检索与OOD评测

`rag_cases.json` 提供 10 条基于当前新闻库的样例。默认只评估检索并计算 Recall@K，不调用聊天模型：

```powershell
D:\Anaconda\envs\fastapi311\python.exe -m evaluation.evaluate_rag
```

需要同时生成答案时：

```powershell
D:\Anaconda\envs\fastapi311\python.exe -m evaluation.evaluate_rag --generate-answers
```

结果写入 `evaluation/rag_results.json`。样例只用于建立评测流程，不能代表项目已经达到某个准确率；后续应增加人工核验的问题、目标文档和参考答案。

## 距离度量候选集合

下面的命令把旧 L2 集合的文档、metadata和已有embedding复制到独立cosine集合。源集合不会删除或覆盖：

```powershell
D:\Anaconda\envs\fastapi311\python.exe -m evaluation.build_cosine_candidate
```

目标已存在时脚本默认拒绝覆盖。只有明确重建候选集合时才使用：

```powershell
D:\Anaconda\envs\fastapi311\python.exe -m evaluation.build_cosine_candidate --recreate-target
```

## L2 / cosine / OOD比较

`rag_cases.json` 包含10条精确新闻问题，`rag_broad_cases.json` 包含6条宽泛新闻问题，`rag_ood_cases.json` 包含20条应被拒绝的领域外问题。

```powershell
D:\Anaconda\envs\fastapi311\python.exe -m evaluation.compare_chroma_metrics
```

脚本输出 Recall@1/3/5、正常/宽泛/OOD最高分分布、检索延迟，以及0.2、0.3、0.4、0.5各阈值下的OOD拒绝率。结果写入被Git忽略的 `evaluation/metric_comparison_results.json`。
