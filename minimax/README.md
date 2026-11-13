# 规有所依 · 工单开发上下文

给开发助手提供一项工单改动所需的规格、代码和测试，并在进程重启后恢复开发进度。样例任务给 `listIssues` 添加负责人筛选。工程资料由 seekdb 提供，进度和验证结果保存在 PowerContext，MiniMax 实际生成了提交的功能实现。

## 快速验收

需要 Node.js 22.12+、pnpm 10.33.0、Python 3.11+（验收用 3.13）及约 2 GB 可用内存。seekdb 原生运行库支持 Windows x64、Linux glibc x64/arm64 和 macOS 15.6.1+ Apple Silicon。保留 npm 可选依赖，分别在目标系统安装。

在本目录执行以下命令。可先用 `python -m venv .venv` 创建虚拟环境，激活后安装。Windows PowerShell 用 `.\.venv\Scripts\Activate.ps1`，Linux 和 macOS 用 `source .venv/bin/activate`。在精简版 Ubuntu 上，请先安装系统的 `python3-venv`。

```sh
npm install --global pnpm@10.33.0
pnpm install --frozen-lockfile
python -m pip install -r requirements.txt
pnpm verify
```

`pnpm verify` 每次在独立目录创建空的 seekdb 和 PowerContext 数据。预期最后一行以 `Verified initialization` 开头。输出在 `.runtime/verification/`，其中 `summary.json` 关联每条命令、退出码、版本及文件，`03-before-checks.json` 和 `08-after-v2.json` 内有完整 TAP 测试日志。脚本会从两次 MiniMax 输出重组实现，并和提交代码逐字核对。复现使用已提交的模型记录和实现。依赖下载完成后，验证流程在本机运行。

PowerContext 若不在 PATH，设 `POWERCONTEXT_EXE` 为已安装的 1.4.1 可执行文件全路径。数据库和 PowerContext 由程序启动。PowerContext 自动分配本机端口和每次启动的随机令牌，服务状态通过本地 SQLite 文件持久化。首次启动数据库和首次迁移 PowerContext 需要数秒。数据目录应可写。

## 验证样例与调用入口

输入是任务 ID，输出为 JSON。默认使用 `.runtime/demo`。按表中顺序执行可观察每一步，步骤 1–9 须共用一个数据目录。

| 步骤 | 命令 | 预期观察 |
| --- | --- | --- |
| 1 | `pnpm sdd import v1` | seekdb 写入 11 条工程资料，返回真实引擎版本和写入条目。 |
| 2 | `pnpm sdd context assignee-filter` | `progress.status` 为 `empty`。返回 8 条来源及行号、内容、摘要值和 SQL 参数。 |
| 3 | `pnpm sdd check assignee-filter before` | 目标测试 0 通过、4 失败、退出 1，回归测试 3 通过、退出 0。外层 CLI 识别预期结果并返回 0，将进度写入 Atomic Memory。 |
| 4 | `pnpm sdd context assignee-filter` | 新进程读回步骤 3 的 Artifact 与修订号。下一步变为「让 MiniMax 实现负责人筛选」，并携带已有验证结果。 |
| 5 | `pnpm sdd import v2` | 重新导入更新后的规格，`ISS-101` 新增制表符和全角空格的明确处理规则。 |
| 6 | `pnpm sdd context assignee-filter` | 返回 v2 的实际文件内容与新哈希，历史结果可见，`nextAction` 提示按新规格重新跑基线。 |
| 7 | `pnpm sdd check assignee-filter before` | 保存 v2 下的基线，Atomic Memory 产生新修订。 |
| 8 | `pnpm sdd check assignee-filter after` | 相同测试哈希，目标 4 项、回归 3 项全部通过。验证结果保存到同一 Artifact。 |
| 9 | `pnpm sdd context assignee-filter` | 从新进程恢复 `complete` 状态和 7 项通过的证据，待办为空。 |

**必需来源**为 `ISS-101`、`ISS-102`、`ISS-103`、`before/list.mjs#listIssues`、`tests/assignee.test.mjs` 和 `tests/regression.test.mjs`。上下文另附测试加载器和 [6 条初始工单](fixture/issue-list/initial-data.mjs)。例如查询负责人 ` lIn ` 命中 `T-08、T-03、T-07`，同时限定 `status=open`、标题含 `cache` 时仅命中 `T-08`。既有状态、标题筛选、顺序及引用行为须保持。

另可执行 `pnpm sdd context label-summary`，它只检索标签计数的规格、代码、测试和共用数据。手工重放时，把 `SDD_RUN_DIR` 设为另一个空目录即可重置。Windows PowerShell 示例为 `$env:SDD_RUN_DIR='.runtime/manual-second'`，Linux 或 macOS 为 `export SDD_RUN_DIR=.runtime/manual-second`。

## 代码与实测材料

| 材料 | 位置 |
| --- | --- |
| 修改前后与差异 | [修改前](fixture/issue-list/before/list.mjs) · [修改后](fixture/issue-list/after/list.mjs) · [统一差异](evidence/assignee.patch) |
| MiniMax 初次生成 | [实际输入](evidence/minimax-prompt.txt) · [原始模块](evidence/first-attempt/minimax-answer.md) · [调用记录](evidence/first-attempt/minimax-run.json) · [目标测试日志](evidence/first-attempt/target-tests.tap) |
| MiniMax 修正 | [修正提示](evidence/repair-instructions.txt) · [模型输出](evidence/minimax-answer.md) · [运行参数与代码哈希](evidence/minimax-run.json) |
| 规格导入和上下文 | [初次入库](evidence/verification/01-import-v1.json) · [恢复后上下文](evidence/verification/04-resumed-context.json) · [重新导入后的上下文](evidence/verification/06-updated-context.json) |
| PowerContext 持久化与测试 | [基线测试及首次写入](evidence/verification/03-before-checks.json) · [最终测试及更新](evidence/verification/08-after-v2.json) · [重启后完成状态](evidence/verification/09-completed-context.json) |
| 流程、实现及限制 | [验收结果](evidence/verification/summary.json) · [一页技术说明](TECHNICAL.md) |

样例工单、代码基线、规格和测试均为原创构造。模型生成阶段采用 MiniMax 发布的 SynLogic-7B，通过本项目的输入生成和本地模型调用脚本对接。可选的重新生成步骤见技术说明。上下文模块是单机、单用户的示范工具，一次运行一个写入命令，任务和来源由样例清单配置。
