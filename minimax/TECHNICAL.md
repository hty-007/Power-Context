# 规有所依 · 技术说明

`pnpm sdd` 提供 CLI 调用入口。任务 ID 先映射到业务目标，seekdb 返回适用工程资料，PowerContext 返回已保存的任务状态，CLI 合并为 JSON。开发助手收到同一份资料和已执行的测试结果，再补齐负责人筛选。验收脚本从空数据库重放并比较文件哈希、数据库返回结果和进程间状态。

## 来源选择

导入器从实际文件抽取规格标题及正文、代码和测试，保存文件路径、行号、符号、内容及 SHA-256。seekdb 1.4.0 通过官方 Node 运行库启动，使用 MySQL 协议进行参数化查询。按 `project_id + task_id` 查映射表，再与当前版本和来源表关联。样例的两个任务共享初始数据，各自匹配不同规格、代码和测试。重新导入在同一事务中更新资料和关系，`ISS-101` 从 v1 切至 v2 时可比较实际来源文本、路径和哈希。上下文输出附带实际执行的 SQL、参数、行数和引擎版本。 [seekdb 官方运行库](https://github.com/oceanbase/seekdb-bindings)

## 状态的保存与使用

PowerContext 1.4.1 通过 REST API 保存 `atomic-memory`。任务独立记忆保存规格版本、上下文摘要、已完成和待办、前后代码哈希、测试哈希和退出码。CLI 在独立进程中调用，每次启动和停止本机 PowerContext 进程，并复用数据库文件。保存通过 `POST` 创建，后续使用 ETag 条件更新，读取附带 Artifact ID、修订号与服务端请求 ID。复用已完成基线将下一步推进到模型修改，并作为运行修改后测试的前置条件。适用资料更新时，旧验证结果保留在 `earlierVerification` 中，新上下文重新验证。 [PowerContext 官方文档](https://github.com/oceanbase/powercontext)

## 实际开发与核对

MiniMax 发布的 `MiniMaxAI/SynLogic-7B`（MIT 许可证）通过 llama.cpp b12360 在本机运行。首次输入由 seekdb 查出的 8 条真实来源及 PowerContext 恢复的基线状态生成。MiniMax 的首稿实现了筛选，但工单中已保存的负责人姓名缺少空白清理，4 项目标测试只通过 1 项。根据失败日志缩小修正范围，MiniMax 生成了空值安全且在两侧清除空白的筛选条件。将这行原样替换到模型首稿，验收脚本从两份原文重组代码并和提交快照比对。两次采纳的输入、输出、运行参数和哈希均已归档。 [MiniMax 官方模型卡](https://huggingface.co/MiniMaxAI/SynLogic-7B) · [固定的 GGUF 量化](https://huggingface.co/tensorblock/MiniMaxAI_SynLogic-7B-GGUF/tree/a05e87469d413a5a433358e90880e102184ba5a3) · [llama.cpp b12360](https://github.com/ggml-org/llama.cpp/releases/tag/b12360)

如需再次生成首稿，设置 `LLAMA_SERVER_EXE` 为 llama-server 的全路径，`SYNLOGIC_GGUF` 为固定版本的 `SynLogic-7B-Q3_K_M.gguf` 全路径。模型文件约 3.8 GB。默认用 CPU，可按硬件设置 `SDD_MODEL_GPU_LAYERS` 和 `SDD_MODEL_DEVICE`。重跑第二次推理时，可按模型记录所列参数使用同一版本的 llama-completion，并将权重和提示文件参数指向本机路径。两次推理输出可能因后端而异。

```sh
node scripts/make-prompt.mjs evidence/development-context.json .runtime/prompt.txt
node scripts/request-model.mjs .runtime/prompt.txt .runtime/model-replay
node scripts/make-repair-prompt.mjs evidence/repair-instructions.txt .runtime/repair-chatml.txt
```

负责人筛选目标测试在修改前因结果中多出其他负责人或未指派工单而全部失败。完成修改后 4 项目标测试、3 项回归测试全部通过，测试文件和样例数据的哈希在前后保持一致。 [真实验收日志](evidence/verification/summary.json)
