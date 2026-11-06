# Memory 01：来源撤回后的记忆停用与恢复

| 项目 | 说明 |
| --- | --- |
| 项目 | PowerContext |
| 编号 | `memory-01-source-lifecycle` |
| 总分 | 20 分 |
| 任务 | 按来源依赖停用或恢复 Memory 与 Topic Memory，使搜索和 Context 反映状态并保留历史引用 |
| 提交产物 | 产品仓库 HTTPS URL 和完整 commit SHA |
| 冻结版本 | [PowerContext v1.2.0](https://github.com/oceanbase/powercontext/tree/9db1753434162c0aa7c9f1189a1722e617880a52)，commit `9db1753434162c0aa7c9f1189a1722e617880a52` |

## 一、题目背景

来源（Source）停止使用后，依赖它的记忆条目（Memory）和主题内容（Topic Memory）也需要重新判断是否仍有依据。否则，原始来源已经撤回，保存下来的内容仍可能通过搜索进入后续生成的上下文（Context）。

PowerContext 已支持记忆条目的停用、恢复和历史版本读取。本题把这些能力连接起来，按照调用方提供的依赖关系传播来源状态：失去支持的内容退出当前搜索和上下文，仍有充分支持的内容继续可用，来源恢复后，重新得到支持的内容恢复使用。停用保留正文，历史引用继续指向当时的版本，便于核对过去的结果。

## 二、题目内容

### 2.1 通用要求与范围

每个 scope 是独立的作用域，证据依赖在其中构成有向无环图（DAG）。每个 scope 使用单写者，注册后不改图，不要求处理多个写者同时改图。验收图每个 scope 为 16 至 20 个节点，包括 4 个 Source、8 至 12 条 Memory 和 4 个 Topic Memory。

输入显式提供正文和依赖，无需从自然语言识别关系；绑定期间不外部改写注册的 Memory 正文版本。停用与恢复只改变使用资格，每个节点的正文按一个整体判断可用性：`all` 要求全部父节点可用；`any` 表示任一父节点都能独立支持整份正文。包含多个独立断言的摘要应分别建节点，或使用 `all` 表示需要全部支持。本题不要求撤回后自动改写摘要。

### 2.2 接口与数据

新增 `powercontext.builtin.runtime.source_lifecycle`，导出 `SourceLifecycleError(ValueError)` 和进行字段校验的 `SourceLifecycleEvent`。事件拒绝额外字段：

| 字段 | 要求 |
| --- | --- |
| `node_id`、`reason` | `str`；非空且不能只有空白。 |
| `revision`、`sequence` | `int`；正整数，分别表示生命周期版本与事件序号。 |
| `available` | `bool`；表示事件后的可用状态。 |

`open_builtin_runtime()` 返回的 Runtime 须提供 `lifecycle.for_scope(scope_id)`。服务提供以下异步接口：

```python
service = runtime.lifecycle.for_scope(scope_id)
await service.register_source("source-a", source_ref, revision=3)
await service.register_memory(
    "memory-a", citation, parents=("source-a",), policy="all"
)
await service.register_topic(
    "topic-a", artifact_ref, parents=("memory-a",), policy="any"
)
active_ids = await service.apply(event)
reference = await service.resolve("memory-a", purpose="generation")
audit_reference = await service.resolve("memory-a", purpose="audit")
```

注册参数分别使用已捕获的 SourceRef、可读取的 MemoryCitation 和 Topic Memory ArtifactRef。`apply()` 返回按节点编号排序的全部可用节点列表。

### 2.3 关键行为与约束

| 行为 | 要求 |
| --- | --- |
| 注册身份 | 节点编号在 scope 内唯一。相同注册幂等，不同绑定冲突；所有注册引用必须存在且可读取。 |
| 注册时状态 | 派生节点在注册时立即按父节点的当前可用性计算状态，不得先默认可用再等待后续事件。 |
| 依赖校验 | 派生节点至少有一个父节点，父节点须先注册且不得重复；拒绝缺失父节点、自环和环。 |
| 事件版本 | 事件只能作用于已注册的 Source，`revision` 须精确匹配其生命周期版本。未知 Source 或版本不匹配抛出 `SourceLifecycleError`。 |
| 事件序号 | 每个 Source 的 `sequence` 单调递增。相同事件重放幂等；低序号或同序号不同内容抛出 `SourceLifecycleError`。 |
| 状态传播 | 撤回与恢复均按各层 all/any 策略传播到所有后代。 |
| 原生 Memory | 列表状态、搜索和 Context 反映传播结果。撤回使用非活跃状态，恢复后重新可检索；注册的正文版本不变。 |
| Topic Memory | 无支持的 Topic 不进入原生 Topic 搜索和 Context，恢复后重新出现；原始 ArtifactRef 仍可精确读取。 |
| 生成与审计 | `generation` 对不可用节点报错，可用节点返回原始引用；`audit` 返回原始引用，允许读取撤回前的历史正文。未知节点和未知用途报错。 |
| 持久化与隔离 | 注册、事件和状态持久化，重启后查询一致；不同 scope 中的同编号节点互不影响。 |
| 中断与重试 | 原生 Memory 状态写入失败导致传播中断后，同一事件重试须完成传播。成功事件重放不得新增 Memory 修订。 |

## 三、提交方式

提交基于表中冻结版本的产品仓库 HTTPS URL 和完整 40 位 commit SHA，平台须能获取该仓库及 commit。同一仓库和 commit 可以包含多题实现，按所选题目分别验收。

产品内部实现可以自由调整，包括新增、重构或删除源码文件；不设逐题文件白名单。保留本题声明的公共接口及兼容行为。

判题仅加载产品的 `src/`，使用固定依赖和资源配置。产品自有测试、文档及其他附带文件可以随 commit 提交，但不进入判题运行环境，也不能替换平台的测试、输入数据或评分逻辑。产品源码在验收期间只读。

## 四、评分标准

公开测试和平台必测组全部通过得 20 分；任一必测项失败得 0 分，不按子项计部分分。

| 必测行为 | 通过要求 |
| --- | --- |
| 图与事件 | 注册身份、注册时状态、依赖策略、事件版本和序号符合契约。 |
| 原生传播 | Memory 列表、搜索、Topic 搜索和 Context 的可用状态一致，独立事实不因无关来源撤回而退出。 |
| 引用与用途 | 历史正文可精确读取，生成资格随状态变化，恢复后返回原始引用。 |
| 重启与重放 | 状态持久化、scope 隔离；成功重放无新修订，中断后的重试完成传播。 |

评测通过公开产品接口核对结果，不使用模型裁判或在线服务。

## 五、公开测试与提交前检查

公开测试覆盖多层 all/any、撤回后注册的派生节点、独立事实与共享 Topic、原生搜索和 Context、精确历史引用、重启及事件校验。

公开测试、样例及辅助文件随本目录发布。按[公开自测说明](../../README.md)准备 Python 3.12 和固定依赖后，在产品仓库根目录运行：

```bash
make -C competition test-lifecycle
```

提交前核对注册与事件后的可用状态、生成和审计用途、恢复后的原始引用，以及成功重放和失败重试的 Memory 修订。公开测试通过后仍须满足全部必测行为。输入为测试生成的证据图，依赖、all/any 策略和撤回恢复事件均显式给出；不要求从自然语言抽取关系。
