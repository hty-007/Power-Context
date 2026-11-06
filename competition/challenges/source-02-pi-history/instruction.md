# Source 02：Pi 会话分支采集与断点恢复

| 项目 | 说明 |
| --- | --- |
| 项目 | PowerContext |
| 编号 | `source-02-pi-history` |
| 总分 | 20 分 |
| 任务 | 采集 Pi 所选分支的完整原始可见事件，在切换分支、追加和重启后恢复采集并回查证据 |
| 提交产物 | 产品仓库 HTTPS URL 和完整 commit SHA |
| 冻结版本 | [PowerContext v1.2.0](https://github.com/oceanbase/powercontext/tree/9db1753434162c0aa7c9f1189a1722e617880a52)，commit `9db1753434162c0aa7c9f1189a1722e617880a52` |

## 一、题目背景

Pi 编程助手的会话记录包含对话、工具调用及执行结果，也是后续排查和复用经验的依据。PowerContext 已有 Pi 插件，能够召回项目知识并采集符合条件的用户输入；完整会话文件中的分支历史尚未接入这条证据链路。

[Pi 的会话格式](https://github.com/earendil-works/pi/blob/6fb2e7815167e6b19006fc526d1a5d0f5f998787/packages/coding-agent/docs/session-format.md) 通过父节点关系组织分支：所选末端节点及其祖先确定一条历史路径。本题采集这条路径上的原始可见事件，保留压缩摘要之前的完整证据。采集进度须持久化，失败后保留原进度，重启后继续；切换分支时共享历史不重复，已保存事件仍能按引用读回，包括完整工具结果。这种历史采集与供模型继续对话的压缩上下文用途不同。

## 二、题目内容

### 2.1 通用要求

本题只接入 Pi，实现所选分支的原始事件解析、Source 文本投影和增量采集。Source 保存事件原文，checkpoint 表示持久化的采集进度。沿用现有 Source Definition、projection、ConnectorLifecycle 和 Runtime 公共接口，由 ConnectorLifecycle 保存成功运行后的进度。

不增加依赖，不维护模块级 checkpoint，不执行记录中的代码或工具命令，不调用网络或在线模型。大工具结果须完整保存在 Source，Memory 的单条文本限制不能成为截断 Source 的理由。分支切换、追加、重启及失败后的恢复以已保存的事件和进度为依据，具体行为见以下合同。

### 2.2 接口与数据

在 `powercontext.sources.pi_session` 提供 `PiSessionInput`、`PiEventInput`、`parse_pi_session`、`PI_EVENT_SOURCE_DEFINITION` 和 `PiSessionConnector`。Source 名为 `pi-event`，connector 名为 `pi-session-history`，版本均为 `1`。

### 2.3 所选分支的完整解析

`PiSessionInput(content: bytes, leaf_id: str | None = None)` 接受 UTF-8（允许 BOM）JSONL。首条必须为 `type=session`、`version=3`，且 `id` 为非空字符串。其余记录有唯一 `id` 和 `parentId`；parent 必须为空或指向已出现节点。非法编码、JSON、重复 ID、缺失 parent、循环、缺失 leaf 都抛出 `ValueError`，不返回部分结果。未指定 leaf 时选最后节点。

`parse_pi_session` 返回所选叶节点完整祖先链上的 `tuple[PiEventInput, ...]`，从根到叶排列。控制节点保持图上的连接关系，不生成文本证据。每个可见事件保留 `session_id`、`event_id`、`parent_id`、`event_type`、`role`、`content` 和完整输入 `record`。只忽略没有可见正文的事件；压缩和分支摘要不能删除它们之前的证据。

| 事件 | 正文规则 |
| --- | --- |
| message 字符串/text 块 | 原样保留；块按换行连接 |
| message 的 toolCall | `[tool_call NAME ID] COMPACT_SORTED_JSON_ARGUMENTS` |
| role=toolResult | `[tool_result TOOL_NAME TOOL_CALL_ID] TEXT` |
| role=bashExecution | `$ COMMAND` 后换行加 OUTPUT |
| compaction / branch_summary | role 为事件类型，正文为 summary |
| custom_message | role 为 custom，正文为可见字符串/text 块 |

JSON 参数保留 Unicode，键排序，不增加空格。最终 content 为 `ROLE: BODY`；thinking、image 与控制事件不进入文本。

### 2.4 不可变事件与精确引用

Source 为 `CAPTURED`。identity 仅由 session ID 和 event ID 决定，稳定且不超过 256 字符；不同事件不能碰撞。同事件改写不能覆盖已经持久化的原始记录。

标准 `powercontext.text-evidence/1` projection 的 source_type 为 `pi-event`，source_id 与 Source name 一致，content 为完整事件文本，metadata 保留 session_id、event_id、parent_id、event_type、role。`registry.read(source)` 返回对应 `PiEventInput`。

每个可见事件都必须经 Runtime ingestion 持久化。检索引用回读后必须能定位到该事件的完整正文和身份，不能只验证某个角色的第一条记录。大工具结果仍需完整保存在 Source；Memory 的单条文本限制不允许成为截断 Source 的理由。

### 2.5 增量与重启

`PiSessionConnector(path, leaf_id=None)` 每次完整解析文件和选定分支，仅提交该分支上尚未导入的可见事件。checkpoint 必须通过现有 ConnectorLifecycle 持久化，保存足够的已导入事件身份和原始记录校验信息，以便重启后去重并发现历史改写。其 JSON 编码、键名和内部结构由实现选择；验收只检查恢复后的导入结果、历史记录及进度是否正确。

切换分支后，共享祖先不重复，新分支事件完整导入；另一分支已采集的记录仍保留为历史证据。文件追加与进程重启遵循相同规则。当前输入必须先完整解析，成功持久化后才由 ConnectorLifecycle 提交 checkpoint；失败保留已提交进度。已导入事件被改写时抛出 `ValueError`。修复输入后可以从原进度继续。

## 三、提交方式

提交基于表中冻结版本的产品仓库 HTTPS URL 和完整 40 位 commit SHA，平台须能获取该仓库及 commit。同一仓库和 commit 可以包含多题实现，按所选题目分别验收。

产品内部实现可以自由调整，包括新增、重构或删除源码文件；不设逐题文件白名单。保留本题声明的公共接口及兼容行为。

判题仅加载产品的 `src/`，使用固定依赖和资源配置。产品自有测试、文档及其他附带文件可以随 commit 提交，但不进入判题运行环境，也不能替换平台的测试、输入数据或评分逻辑。产品源码在验收期间只读。

## 四、评分标准

本题共 20 分。全部必测行为通过得 20 分，任一失败得 0 分，不按子项计部分分。

| 必测组 | 验收结果 |
| --- | --- |
| 完整解析 | 所选分支全部可见节点、工具调用、结果及摘要符合固定预期；控制节点不破坏祖先关系 |
| 持久化与引用 | 逐事件 Source 与 projection 完整保存，重启后可检索并按精确引用回读 |
| 增量与失败 | 分支切换只导入新节点；追加和重启去重；损坏输入或历史改写不推进 checkpoint |

## 五、公开测试与提交前检查

公开测试检查历史路径、原始事件、分支切换、追加与重启恢复。公开样例包含一份转换后的 Pi v3 会话及所选叶节点。

公开测试、样例及辅助文件随本目录发布。按[公开自测说明](../../README.md)准备 Python 3.12 和固定依赖后，在产品仓库根目录运行：

```bash
make -C competition test-pi
```

提交前检查：

- 检查默认及显式叶节点、控制节点连接、工具调用与结果，以及压缩摘要之前的原始事件。
- 核对完整 record、正文和逐事件引用，不把模型压缩视图作为原始事件取证结果。
- 检查切分支、追加、重启、损坏后修复和已导入记录改写时的持久化进度。
- 核对仓库 URL、完整 commit 及公开接口。公开自测通过后仍须满足全部必测行为。

公开输入来自 [Trace Commons](https://huggingface.co/datasets/trace-commons/agent-traces)，固定 revision 为 `147684b2fdacbbdcd473e92962f565902b2739d5`，归属 Trace Commons contributors。样例转换为 Pi v3 形状，不是原生 Pi 会话：保留可见文字、工具参数及结果，按原顺序建立 parent 链，思考、图片及系统记录不生成文本事件。分支、控制节点和摘要另用明确构造的输入覆盖。公开样例保留 CC BY 4.0 署名；上游数据卡的许可声明不替会话内第三方材料重新授权。
