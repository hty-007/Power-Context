# Context 02：上下文增量传输与完整重建

| 项目 | 说明 |
| --- | --- |
| 项目 | PowerContext |
| 编号 | `context-02-incremental-pack` |
| 总分 | 20 分 |
| 任务 | 复用上一轮上下文发送增量数据，完整重建本轮正文，并减少计入协议开销的传输字节 |
| 提交产物 | 产品仓库 HTTPS URL 和完整 commit SHA |
| 冻结版本 | [PowerContext v1.2.0](https://github.com/oceanbase/powercontext/tree/9db1753434162c0aa7c9f1189a1722e617880a52)，commit `9db1753434162c0aa7c9f1189a1722e617880a52` |

## 一、题目背景

连续请求准备出的上下文经常包含重复内容。PowerContext 的普通 `prepare` 每次返回完整正文；当客户端已经持有上一轮结果时，重复发送这些字节会增加传输量。

本题把完整上下文表示为 Context Pack，即带有分块和校验信息的数据包。首次发送完整包，后续发送增量包 delta，指明上一包可以保留多少前缀块，以及需要补上的后缀。接收端仅凭已有 Pack 和收到的 delta，就应恢复出与普通 `prepare` 完全一致的正文和引用。

[Xdelta](https://jmacd.github.io/xdelta/)也使用已有内容重建新内容。本题采用下文约定的前缀复用协议，评测会计入首次完整包、编码和校验字段的全部字节，核对连续请求的净传输节省。传输形式改变后，接收端最终得到的上下文内容保持一致。

## 二、题目内容

### 2.1 通用要求与范围

增量接口接入 `ScopedContextApplication`，通过既有 `prepare` 取得本轮应当发送的目标内容。正文、精确引用（citation）的语义以及普通 prepare 的行为保持不变。

接收端应仅凭序列化数据完成解码和重建：首次使用完整 pack，后续以已有的完整包作为 base pack，应用收到的 delta，得到本轮目标包。重建不能依赖发送端的进程内缓存或共享 Python 对象。分块和前缀选择可以采用不同方法，但须满足下文的互操作协议与实际传输成本要求。

普通 prepare 的合法空结果 `content=None` 对应空文本和空 pack，增量重建后得到空字节。不同 Memory 修订版本（revision）的引用身份不要求相同。禁止按固定轮次、Memory 正文或 workload 身份返回预置结果。

### 2.2 接口与数据

新增模块 `powercontext.builtin.runtime.context_pack`，提供以下公开接口：

| 接口 | 契约 |
| --- | --- |
| `ContextPack.build(items)` | 从 bytes 元组构造完整 pack，提供 `items`、`digest` 与 `byte_count` |
| `ContextPack.from_content(content, *, chunk_bytes=512)` | 确定性分块，保持原始字节；每块有效 UTF-8，且不超过调用方指定上限 |
| `ContextPack.to_bytes/from_bytes` | 序列化和校验完整 pack |
| `ContextPackDelta.between(base, target)` | 选择有效公共前缀并携带目标后缀，不要求最长前缀 |
| `ContextPackDelta.to_bytes/from_bytes` | 序列化和校验 delta |
| `ContextPackDelta` | 字段为 `base_digest`、`target_digest`、`prefix_items`、`appended`；`appended` 为 bytes 元组 |
| `apply_context_pack_delta(base, delta)` | 校验并返回目标 `ContextPack` |
| `ContextPackError` | 协议或分块校验错误 |
| `ScopedContextApplication.prepare_incremental(request, base_pack=None)` | 未提供 base 时返回完整 `ContextPack`，提供合法 base 时返回 `ContextPackDelta` |

wire 字段和编码是接收端互操作契约：

| 对象 | 字段与编码 |
| --- | --- |
| Pack | 仅包含 `schema`、`items`、`digest`、`byte_count`；schema 为 `powercontext.context-pack.v1` |
| Delta | 仅包含 `schema`、`base_digest`、`target_digest`、`prefix_items`、`appended`；schema 为 `powercontext.context-pack-delta.v1` |
| 分块 | wire 的 `items`、`appended` 为标准 Base64 字符串数组，内存中为 bytes 元组 |
| JSON | UTF-8 编码，key 排序，分隔符为 `,` 和 `:`，不含额外空白 |
| Pack 摘要 | 对仅含 `schema`、`items` 的上述 JSON 求 SHA-256，表示为 `sha256:<hex>` |
| Pack 字节数 | `byte_count == len(pack.to_bytes())`，包括该字段自身 |
| Delta 字节数 | `delta.byte_count == len(delta.to_bytes())`，该属性不作为额外字段写入 delta JSON |

### 2.3 关键行为与约束

`from_content` 默认分块上限为 512 bytes。分块可以按行、固定容量或其他确定性方式实现；每块须满足有效 UTF-8 与调用方字节上限，重组后保持原始正文。正数上限内无法形成合法边界时抛出 `ContextPackError`。空 pack 合法。

`prefix_items` 为非负整数，不接受布尔值或浮点数，且不能超过 base 分块数。任意有效公共前缀均可使用，包括 0；接收端将保留的 base 前缀与 `appended` 拼接后，须得到 target 的完整分块、顺序、正文和摘要。传输收益由实际 wire bytes 判定，不要求某种前缀选择算法。

缺字段、额外字段、非法编码、base 摘要不符、非法 prefix、prefix 越界或 target 摘要错误以 `ContextPackError` 失败。完整 pack 解码时也须校验摘要和字节数。

## 三、提交方式

提交基于表中冻结版本的产品仓库 HTTPS URL 和完整 40 位 commit SHA，平台须能获取该仓库及 commit。同一仓库和 commit 可以包含多题实现，按所选题目分别验收。

产品内部实现可以自由调整，包括新增、重构或删除源码文件；不设逐题文件白名单。保留本题声明的公共接口及兼容行为。

判题仅加载产品的 `src/`，使用固定依赖和资源配置。产品自有测试、文档及其他附带文件可以随 commit 提交，但不进入判题运行环境，也不能替换平台的测试、输入数据或评分逻辑。产品源码在验收期间只读。

## 四、评分标准

全部功能与传输成本要求通过得 20 分，任一要求失败得 0 分。功能门通过后才执行成本测量；缺失接口、跳过必测项或空收集均不通过。

| 验收类别 | 通过条件 |
| --- | --- |
| 协议 | 字段、编码、摘要、字节数及非法输入符合契约 |
| 重建 | 序列化后独立重建普通 prepare 的目标正文，支持首部、中部、尾部变化及合法空结果 |
| 原生集成 | Runtime 完成首次 prepare、Memory 追加、查询切换及增量 prepare |
| 测量序列 | 从 8 个真实 meeting turn 开始，共 40 轮，每轮非空，至少产生四种不同 Context |
| 净传输节省 | `1 - incremental_bytes / raw_full_bytes >= 0.45` |

`raw_full_bytes` 是每轮普通 prepare 完整正文的 UTF-8 字节数之和。`incremental_bytes` 是实际发送的首次完整 pack 和后续 delta 序列化字节数之和，计入 JSON、Base64、摘要及协议字段的全部开销；使用 `len(to_bytes())` 计量。

另报告完整 v1 pack 的累计字节，以及相同正文逐轮 `gzip.compress(compresslevel=9, mtime=0)` 的累计字节，gzip 不复用跨请求状态。分别计算增量协议对两种对照的节省率，负值表示增量协议更大，须如实报告。这两项均为观测对照，不要求优于 gzip，45% 门槛也不代表压缩传输优势。

40 轮成本序列的非空要求不改变普通接口对合法空结果的支持。

## 五、公开测试与提交前检查

公开测试检查序列化、增量重建、原生 prepare 集成及实际传输字节，并提供 QMSum 会议片段和公开测量配置。

公开测试、样例及辅助文件随本目录发布。按[公开自测说明](../../README.md)准备 Python 3.12 和固定依赖后，在产品仓库根目录运行：

```bash
make -C competition test-incremental
```

提交前检查：

- 固定 wire 字段、Base64、摘要和实际协议字节数正确。
- 分块有界、有效且确定性，任意有效 prefix 都可正确解码。
- 正文各位置变化和空结果可重建，错误 base、prefix 或摘要按契约失败。
- 成本分母使用普通正文 UTF-8 bytes，首次 pack 和全部协议开销计入分子。
- 普通 prepare 保持兼容，仓库 URL 和完整 commit 可获取，公开接口可用。

公开测试通过不代替完整 40 轮序列的净传输成本验收。

会议正文来自 [QMSum](https://github.com/Yale-LILY/QMSum/tree/83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e)，固定 commit 为 `83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e`，上游仓库采用 MIT 许可。公开样例保留 speaker、原始 turn 顺序和正文；追加时刻、检索词与查询切换是明确的测量条件。
