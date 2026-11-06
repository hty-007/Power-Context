# 公开 SciFact 样例来源

retrieval-01-reranking、retrieval-02-hybrid-search 与 context-01-evidence-selection 共用同一条 BEIR SciFact test claim 和 8 篇真实论文摘要。来源文件 `scifact-sample.json` 的 SHA-256 为 `9b1e70a997e55391485d578d04906b828103d83fc5c5a5099faf3528b1af91ad`。

数据下载来源为 [BEIR SciFact](https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip)，原始 `scifact.zip` SHA-256 为 `536e14446a0ba56ed1398ab1055f39fe852686ecad24a6306c80c490fa8e0165`；SciFact 仓库固定在 commit `68b98a56d93e0f9da0d2aab4e6c3294699a0f72e`。[SciFact 上游许可](https://github.com/allenai/scifact/blob/68b98a56d93e0f9da0d2aab4e6c3294699a0f72e/LICENSE.md)规定该版本的 claims 和 evidence annotations 使用 CC BY 4.0，来源论文摘要使用 S2ORC 的 ODC-By 1.0。再分发时应分别保留上游署名与许可信息；BEIR 格式不替代这些许可。

公开样例仅包含 claim 和摘要，三题的补充 query/doc ID 均排除此公开 claim 和摘要。
