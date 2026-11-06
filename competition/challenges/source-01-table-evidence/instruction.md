# Source 01：CSV 行级采集与原始记录回查

| 项目 | 说明 |
| --- | --- |
| 项目 | PowerContext |
| 编号 | `source-01-table-evidence` |
| 总分 | 20 分 |
| 任务 | 按主键逐行采集离线 CSV，使检索引用能够回查完整原始记录 |
| 提交产物 | 产品仓库 HTTPS URL 和完整 commit SHA |
| 冻结版本 | [PowerContext v1.2.0](https://github.com/oceanbase/powercontext/tree/9db1753434162c0aa7c9f1189a1722e617880a52)，commit `9db1753434162c0aa7c9f1189a1722e617880a52` |

## 一、题目背景

表格中的一条检索结果，往往需要对应到一条完整记录。引用整个 CSV 不便于核对，引用行号又会随着排序变化。PowerContext 已能保存原始文本并关联检索引用，但通用文本来源没有表头、主键和行级校验语义。

本题将每行采集为独立的 Source，即保存下来的原始证据；按文档和主键识别记录，并保留原列序与字段值。记录经现有流程持久化后，检索引用应能回查对应行，重新排序或进程重启后仍然一致。[LlamaIndex CSVReader](https://developers.llamaindex.ai/python/framework-api-reference/readers/file/) 也支持逐行生成文档；本题进一步明确行身份、字段保真和持久化回查的要求。

## 二、题目内容

### 2.1 通用要求

实现 CSV 解析、行级 Source 和标准文本投影，沿用现有 Source Definition、Runtime 和 Memory 公共接口。Runtime 负责持久化，文本投影则提供 Memory 检索流程使用的内容表示。每行形成 `CAPTURED` Source，即保存已采集的行内容；读取和投影都须保留原始字段、列顺序及值。

输入完全离线。不增加依赖，不调用模型或网络服务。测试提供固定 Memory pipeline，无需新增 pipeline 或导出 API；本题验收逐行采集后的完整记录及精确引用回查。

### 2.2 接口与数据

在 `powercontext.sources.csv_rows` 提供以下接口：

```python
class CSVDocumentInput(BaseModel):
    document_id: str
    primary_key: str
    content: bytes

class CSVRowInput(BaseModel):
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

class CSVRowSource(Source):
    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]: ...

def csv_row_source_id(document_id: str, primary_key_value: str) -> str: ...

CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput]
```

### 2.3 CSV 解析

`parse_csv_document` 接收完整的离线字节内容，按严格 UTF-8 解码，并接受文件开头的 UTF-8 BOM。解析遵循 Python 标准库 `csv.reader` 的固定语义：逗号分隔、`"` 为 quote 字符、双引号用 `""` 转义，并采用 `strict=True` malformed quoting 规则。实现可以使用任何等价正确的方法，不会按内部调用方式评分。

第一条记录是表头。表头字段必须非空、非纯空白且唯一；列名按原值精确比较，不自动去空格。首尾带空格的列名有效，且只能由完全匹配的 `primary_key` 指定。`primary_key` 必须非空。`document_id` 必须非空且不含首尾空白字符。数据记录的字段数必须等于表头宽度，主键值不能是空字符串或纯空白，且在同一文档内唯一。主键值和字段内容作为原样字符串保留，不做 trim、数值、日期或布尔类型转换；首尾带空格的非空主键按原值区别和编码。

表头之后的完全空物理记录忽略；只有表头的文档有效，解析结果为空 tuple。空文件或空表头无效。整个文档必须先解析并验证成功，再返回 tuple；任何编码、CSV 格式或数据校验错误都以 `ValueError` 失败，不得返回部分行。

### 2.4 Row Source 和投影

`CSVRowInput` 包含 `document_id`、`primary_key`、`primary_key_value`、按原表头顺序排列的 `columns: tuple[str, ...]` 和 `values: tuple[str, ...]`。每个 `CSVRowInput` 解析为一个 materialization 为 `CAPTURED` 的 `CSVRowSource`，其 `name` 与 `source_id` 均为该行 identity。`CSVRowSource` 还保留 identity 组成字段及 `columns`、`values`。Source Definition 的 `name` 为 `csv-row`，版本为 `1`，`input_class` 为 `CSVRowInput`，`source_class` 为 `CSVRowSource`。identity 使用固定格式：

```text
csv:{percent-encoded document_id}:{percent-encoded primary_key_value}
```

两个组件分别按 `urllib.parse.quote(component, safe="")` 百分号编码；生成的 `source_id` 不得超过 256 个字符。`primary_key` 保留原样并精确匹配列名，不能通过 trim 改变含空格的有效列名。

Source Definition 提供标准 `powercontext.text-evidence` projection。`TextEvidence` 的 `source_type` 为 `csv-row`，`source_id` 与 Source identity 相同。`content` 是该行按原表头顺序构造的 JSON 对象，使用 `ensure_ascii=False` 和紧凑分隔符 `(',', ':')` 序列化。`metadata` 必须恰好包含 `document_id`、`primary_key`、`primary_key_value`、`columns` 和 `row` 五个键；`row` 是保留列顺序的完整字段对象。Projection 不把数字或空字段改写为其他类型。

Source 须能通过真实 `SourceDefinitionRegistry` resolve/read、生成 manifest 和 observation，并经 Runtime ingestion 持久化。重启后，全部行的 projection 保持一致；检索结果通过精确 `MemoryCitation` 回查目标行。测试提供固定 Memory pipeline，无需新增 pipeline 或导出 API。

## 三、提交方式

提交基于表中冻结版本的产品仓库 HTTPS URL 和完整 40 位 commit SHA，平台须能获取该仓库及 commit。同一仓库和 commit 可以包含多题实现，按所选题目分别验收。

产品内部实现可以自由调整，包括新增、重构或删除源码文件；不设逐题文件白名单。保留本题声明的公共接口及兼容行为。

判题仅加载产品的 `src/`，使用固定依赖和资源配置。产品自有测试、文档及其他附带文件可以随 commit 提交，但不进入判题运行环境，也不能替换平台的测试、输入数据或评分逻辑。产品源码在验收期间只读。

## 四、评分标准

本题共 20 分。全部必测行为通过得 20 分，任一失败得 0 分，不按子项计部分分。

| 必测组 | 通过条件 |
| --- | --- |
| CSV 解析和原子校验 | 完整评测 12 行数据字段和值匹配；BOM、多行引号、空行、表头、主键、宽度、编码和 malformed CSV 边界符合契约；输入错误不返回部分结果 |
| 稳定 identity 和 TextEvidence | 行级 Source ID 稳定、无分隔符碰撞且不超过长度上限；`read` 与完整 projection 保留列序和值 |
| Runtime 持久化和引用 | 公开和补充 workload 均经 manifest/observation 真实提交；重启后所有 projection 匹配；Memory 搜索结果的精确 citation 可回查目标行 |

## 五、公开测试与提交前检查

公开测试检查 CSV 解析、主键与字段保真、行身份及原生持久化引用。公开样例为 12 条完整机场记录。

公开测试、样例及辅助文件随本目录发布。按[公开自测说明](../../README.md)准备 Python 3.12 和固定依赖后，在产品仓库根目录运行：

```bash
make -C competition test-csv
```

提交前检查：

- 核对原始字符串、含空格的列名和主键，避免类型转换或 trim 改变输入。
- 检查错误输入的原子失败、身份编码与长度，以及重排后的稳定性。
- 检查真实入库、重启后的完整投影和精确引用回查。
- 核对仓库 URL、完整 commit 及公开接口。公开自测通过后仍须满足全部必测行为。

公开输入包含 12 条 [OurAirports](https://ourairports.com/data/) 机场记录，取自 `davidmegginson/ourairports-data` 的固定 commit `44d4715eca5a7b01b88f0be00ed62dcca309b633`。字段名、列序和值保持原样，`id` 为稳定主键。官方数据为 Public Domain，固定版本仓库采用 Unlicense；公开样例保留来源及许可。
