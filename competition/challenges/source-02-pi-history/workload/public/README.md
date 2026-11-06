# 转换 Pi 输入的来源

`converted-pi.jsonl` 由 Trace Commons 原生 Claude Code 会话转换为 Pi v3 形状，**不是原生 Pi 会话**。本题只要求 Pi 接口，不要求解析上游格式；原始会话不随题目分发。

| 项目 | 值 |
| --- | --- |
| 来源 | [trace-commons/agent-traces](https://huggingface.co/datasets/trace-commons/agent-traces) |
| 固定 revision | `147684b2fdacbbdcd473e92962f565902b2739d5` |
| 原始路径 | `sessions/claude_code/4c09dfa9-ff25-4b4c-a2d0-d2fd702c64ee.jsonl` |
| 原始文件 SHA-256 | `137dd4872992c3a1b4d6460962a6d5b2578141511af97bc291039dd6e3b5ea6f` |
| 许可 | 数据卡声明 [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) |
| 归属 | Trace Commons contributors；本题转换保留上述许可 |

转换保留可见文字、工具参数及工具结果；原始记录顺序形成 parent 链。`tool_use` 转成 `toolCall`，`tool_result` 转成 `toolResult`；上游结果未提供工具名时使用 `unknown`。思考、图片及系统记录不生成文本事件。记录中的代码和工具命令只作为数据保存，不执行。

`cases.json` 记录输入文件、选定叶节点及转换后文件校验和，不包含标注答案。分支与摘要验收使用明确构造的测试输入。
