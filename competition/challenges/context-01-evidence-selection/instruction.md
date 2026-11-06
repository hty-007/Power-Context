# Context 01：按查询和预算选择原文证据

| 项目 | 说明 |
| --- | --- |
| 项目 | PowerContext |
| 编号 | `context-01-evidence-selection` |
| 总分 | 20 分 |
| 任务 | 依据查询从已召回的记忆中选择原文片段，使正文、引用和格式共同满足 UTF-8 字节预算 |
| 提交产物 | 产品仓库 HTTPS URL 和完整 commit SHA |
| 冻结版本 | [PowerContext v1.2.0](https://github.com/oceanbase/powercontext/tree/9db1753434162c0aa7c9f1189a1722e617880a52)，commit `9db1753434162c0aa7c9f1189a1722e617880a52` |

## 一、题目背景

检索命中一条相关记忆后，有用的证据可能只占正文的一小部分。PowerContext 会把选中的材料组装为供本轮模型调用使用的上下文。现有组装方式主要按候选顺序装入内容，并在空间不足时截取前缀，正文中后部的关键内容可能因此丢失。

本题根据当前查询，从已召回的记忆正文中选择相关原文片段。[LlamaIndex 的句子筛选组件](https://developers.llamaindex.ai/python/framework/module_guides/querying/node_postprocessors/node_postprocessors/#sentenceembeddingoptimizer)具有类似用途。调用方应能沿片段的引用和字符位置核对原文；输出预算则按最终上下文的 UTF-8 字节数计算，包含正文、引用和格式。评测使用 SciFact 人工标注，检查有限空间内保留了多少相关证据。

## 二、题目内容

### 2.1 通用要求与范围

为 `PrepareContextRequest` 增加默认关闭的 `evidence_selection: bool`。开启后，`PreparedContextBuilder.build_scopes_result` 根据 `request.query`，从传入的 `PreparedMemoryCandidates` 中选择相关原文片段。

输入候选已由检索阶段提供，本题负责在候选正文内部选择片段。片段必须直接取自原文，并保留精确来源和位置，不能用改写、翻译或生成的摘要替代。完整输出须满足调用方提供的字节预算。

选择模式只要求处理 Memory 候选，不要求同时装入 Code、TopicMemory 或 Experience。关闭该模式时，普通 prepare 的输出和行为应保持兼容。

片段的选择、去重和排序算法由实现决定。评测离线进行，不调用模型或在线服务。实现不得读取评测标注、按题目 ID 返回固定结果，或要求调用方提供标注答案。

### 2.2 接口与数据

`PreparedContextBuild.fragments` 返回片段元组，每个片段包含：

| 字段 | 契约 |
| --- | --- |
| `address: MemoryEntryAddress` | 精确标识 scope、Memory revision、entry ID 和 entry version ID |
| `start: int`、`end: int` | 原 Memory entry 的半开 Python 字符坐标，不能用布尔值或浮点数替代 |
| `text: str` | 原文 `entry.text[start:end]`，不改写、翻译或生成新证据 |

区间须满足 `0 <= start < end <= len(entry.text)`。同一 entry 可以贡献多个不同片段，每个片段都须能通过真实 Memory citation 回查原 entry。

`origins` 与 fragments 的顺序、数量和实际引用逐项对应。本 Scope 的 `MemoryCitation` 或带 Scope 的 `MemoryEntryAddress` 均可按引用语义对应，不强制一种对象表示。相同正文在不同 scope 中不能混用身份。

非空输出保留既有 trust policy，以及 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` 和 `END_POWERCONTEXT_PREPARED_CONTEXT_V1` envelope。中间 JSON 包含 `trust="untrusted_history"` 和 `items`；每项包含：

- `citation`：`scope_id`、`memory_ref`、`entry_id`、`entry_version_id`，指向片段原版本。
- `content`：片段原文。
- `start`、`end`：片段原文字符区间。

无可用片段时可以返回空 Context。内容摘要不能替代 citation 身份。

### 2.3 关键行为与约束

`content_bytes` 必须等于完整输出的 UTF-8 字节数，并且不超过 `max_bytes`。预算包含 trust policy、envelope 标记、JSON、citation 和正文，不得只计算正文长度。

同一候选集合替换 query 后，选择须能反映不同证据需求，包括文档后部证据。所有返回片段都保留精确原文、字符区间、引用和对应 origins，不得通过省略输出开销满足预算。

## 三、提交方式

提交基于表中冻结版本的产品仓库 HTTPS URL 和完整 40 位 commit SHA，平台须能获取该仓库及 commit。同一仓库和 commit 可以包含多题实现，按所选题目分别验收。

产品内部实现可以自由调整，包括新增、重构或删除源码文件；不设逐题文件白名单。保留本题声明的公共接口及兼容行为。

判题仅加载产品的 `src/`，使用固定依赖和资源配置。产品自有测试、文档及其他附带文件可以随 commit 提交，但不进入判题运行环境，也不能替换平台的测试、输入数据或评分逻辑。产品源码在验收期间只读。

## 四、评分标准

全部功能与质量要求通过得 20 分，任一要求失败得 0 分。功能门通过后才执行质量测量；缺失接口、跳过必测项或空收集均不通过。

| 验收类别 | 通过条件 |
| --- | --- |
| 查询与原文 | 查询驱动选择，原字符区间、文本和精确引用一致，scope 身份及 origins 正确 |
| 预算与兼容 | 完整 UTF-8 输出预算正确，默认关闭时保留普通 prepare 行为 |
| 质量规模 | 24 个原始 claim，每个请求包含三篇原始摘要；1,400、1,800 bytes 两档预算，共 48 次请求 |
| 证据词项召回 | 48 次请求的平均 evidence token recall 不低于 0.65 |
| 完整证据比例 | 至少 40% 请求完整覆盖其官方 标注证据 的全部字符区间 |

评分合并同一 标注证据 文档中相交或相邻的已选区间，重复字符不重复计算。文本转为小写，以 `[a-z]+|\d+` 提取英文单词和数字，按词频多重集合求交集。每次请求的 token recall 为已选原文与官方 evidence 的交集词数除以官方 evidence 词数，再对 48 次请求求算术平均。

完整覆盖可以由多个片段共同完成，要求合并区间覆盖该请求所有 标注证据 字符区间。完整请求数除以 48 得到完整证据比例。独立可行性检查确认 标注证据 连同引用及输出 envelope 能装入对应预算，不向实现提供这些标注。

## 五、公开测试与提交前检查

公开测试检查查询驱动的片段选择、原文区间、引用和 UTF-8 预算，并提供 SciFact 的一条 claim 和八篇论文摘要。

公开测试、样例及辅助文件随本目录发布。按[公开自测说明](../../README.md)准备 Python 3.12 和固定依赖后，在产品仓库根目录运行：

```bash
make -C competition test-evidence
```

提交前检查：

- query 改变后能选中对应原文，文档后部及多片段输出正确。
- 原文区间、citation、origins 和 scope 身份一致。
- Unicode 内容按完整 UTF-8 输出计费，预算边界及合法空结果正确。
- 普通 prepare 保持兼容，仓库 URL 和完整 commit 可获取，公开接口可用。

公开测试通过不代替正式证据质量验收。

公开样例来自 [SciFact](https://github.com/allenai/scifact/tree/68b98a56d93e0f9da0d2aab4e6c3294699a0f72e)，固定 commit 为 `68b98a56d93e0f9da0d2aab4e6c3294699a0f72e`。公开样例包含一条 claim 和八篇论文摘要，不包含相关性标签或 evidence 区间。claims 与 evidence 标注采用 CC BY 4.0，论文摘要遵循 S2ORC 的 ODC-By 1.0，公开样例保留署名和对应许可。
