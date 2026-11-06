# 会话文字采样来源

`messages.json` 采样自 Trace Commons 的原生 Claude Code 会话，不是 Pi 会话，也不要求参赛者解析上游格式。

| 项目 | 值 |
| --- | --- |
| 来源 | [trace-commons/agent-traces](https://huggingface.co/datasets/trace-commons/agent-traces) |
| 固定 revision | `147684b2fdacbbdcd473e92962f565902b2739d5` |
| 原始路径 | `sessions/claude_code/0ca72c0c-ac42-400a-8ca6-cc31a787225f.jsonl` |
| 原始 SHA-256 | `5780bf4aaa8af2af35e58e1f25360c669cc07af751a16b8c6bdb1cdd23c40dee` |
| 采样文件 SHA-256 | `f3a4931a5952f5f29422356a0de920b35401177f011db8f0945cfd1cda2faab0` |
| 许可 | 固定 revision 的[数据卡](https://huggingface.co/datasets/trace-commons/agent-traces/blob/147684b2fdacbbdcd473e92962f565902b2739d5/README.md)将 compilation、结构和 metadata 声明为 [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) |
| 归属 | Trace Commons contributors；采样保留归属，trace 内原材料仍受各自原许可约束 |

按原始行顺序读取 message 中的可见 text，保留至少 80 个字符的记录，每条截取前 512 个 Unicode 字符，共 11 条。源文件行号、role 与稳定 source_id 保存在采样文件中；不抽取思考块、图片或工具执行结果。代码、链接及命令仅作为文字输入，不执行。

数据卡说明 trace 依赖捐赠者有权公开发布的认证，不保证逐段第三方材料许可。这里选用会话参与者自己的工作进展叙述，不复制工具读取的源码、第三方文件或 skill 正文；CC-BY-4.0 不覆盖 trace 内仍受原许可约束的材料。

验收通过 `CaptureSource` 写入这些片段。离线 provider 按收到的证据生成带 `CountEvidence` 的确定性结果，便于检查处理是否发生、哪些 Source 被消费以及引用是否准确；它不回答原会话问题，也不评估语言模型质量。
