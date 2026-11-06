# QMSum 数据来源

正文取自 [Yale-LILY/QMSum](https://github.com/Yale-LILY/QMSum/tree/83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e)，固定 commit `83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e`。上游归档 SHA-256 为 `149335019f734df38a3ad4b17e3cb0d539fdd80e71a6f6b966c8f30cc031dc15`，许可见本目录的 `LICENSE`（MIT）。QMSum 收录真实会议转录及人工问题、摘要；本题不使用生成正文。

该题评价增量传输，不评价自然语言检索质量。检索词从会议讨论的产品术语提取，追加时刻与查询切换是人为控制的测量协议，不能视为真实用户请求日志。保留原始 speaker 和完整 turn 文本，不拼接伪造对话。不同样本按不同会议切分。

公开样本来自 `data/ALL/test/ES2004a.json`，原文件 SHA-256 `911e56db0d8482f1e730bae0a8fcf0ab0095c94abb000ba0516e975fa6e48b31`。按原序取正文 UTF-8 长度 120–900 字节且含 remote 的前两条 turn；索引保存在 `turns.json`。派生 SHA-256 `d949c37c1593c716fa15e4ccbf323beda4fd7497998109ffb06938b82de87b1d`。
