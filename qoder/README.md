# 实验溯源台（Experiment Provenance Bench）

[打开 Qoder Sites 演示站](https://exp-provenance-seekdb-4ehl0wm2gg9.qoder.zone/)

面向做科研和课程实验的同学：实验跑过，但原始条件、日志和指标散在几十个 Markdown 里，
下次想回答「这个 accuracy 是在哪个划分上跑的」「和上周那版能不能直接比」就断了线索。
本工具把实验记录导入本地 **seekdb**，用真实 FULLTEXT 索引（ik 分词 + 自然语言模式）检索，
给出带原文行号的证据卡片，并在两条实验之间判断可比性、计算指标变化、导出带证据引用的对比摘要。

数据全部留在本地一个 seekdb 目录里，无账号、无 Docker、无模型、无 API Key，仅监听 `127.0.0.1`。

## 功能对照

| 需求 | 实现位置 |
| --- | --- |
| 导入 Markdown，提取实验名/项目/数据集/划分/指标 | `src/parse.js`、`src/store.js:importRecord` |
| 内容指纹识别重复导入 | `src/parse.js:fingerprint`（sha256，规范化空白后）+ `exp_record.fingerprint` 唯一索引 + 撞唯一索引后换事务重试一次 |
| 记录与指标原子写入 | `src/store.js:withTransaction`（池上借连接 → `BEGIN/COMMIT/ROLLBACK`），失败不留缺指标记录 |
| 并发导入安全 | `mysql2` 连接池（默认 4，`EXPDB_POOL_SIZE` 可调），每个请求独立连接；`src/db.js:open` |
| 多项目、多数据集虚构演示样例 | `samples/`（每份文件即标注「演示数据」） |
| seekdb FULLTEXT 检索中英文 + 项目/数据集筛选 | `src/search.js`：`MATCH(body) AGAINST (?)`（自然语言模式）+ `project = ? / dataset = ?`，索引为 `FULLTEXT ft_exp_body(body) WITH PARSER ik` |
| 命中文段、原记录名、行号 | `src/parse.js:evidenceLines` + `src/search.js:queryTokens`（对同一条查询执行 `SELECT TOKENIZE(?, 'ik')` 取实际词元再定位）+ `exp_metric.line_no`，界面证据卡片 |
| 失配与空查询的清晰提示 | 零命中 → `meta.mode='miss'` 并提示换写法/去掉筛选；空查询 → `meta.mode='empty'` 提示可用检索词，不执行 SQL |
| 两条实验比较指标 + 计算变化 + 数据集/划分 | `src/compare.js:compare` |
| 单位/数据集/划分不一致 → 不可直接比较 | 同上 `issues[]`，界面显示警告并在变化列标注「单位不一致」 |
| 复制带证据引用的 Markdown 摘要 | `src/compare.js:toMarkdown` + 界面「复制 Markdown 摘要」 |
| 检索 SQL / EXPLAIN 证据 | 界面「查看检索 SQL / EXPLAIN」；`pnpm test` 与 `pnpm run evidence` 生成 `docs/seekdb-evidence.md` |

可用 `EXPDB_HOME` 指定新数据目录。播种接口 `POST /api/seed` 通过内容指纹去重。

## 依赖版本

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Node.js | `>=22`（在 Windows x64 上用 `24.22.1` 验证） | 实测服务端为 `5.7.25-OceanBase seekdb-v1.4.0.0` |
| `@oceanbase/seekdb-node` | `1.4.0`（精确） | 本地 seekdb 生命周期绑定，ESM 包；平台原生运行时按 optionalDependencies 分发（Linux x64/arm64 glibc、macOS arm64、Windows x64） |
| `mysql2` | `3.11.5` | 连接池 + 参数化 SQL，锁进 `pnpm-lock.yaml` |

本项目为 ESM（`"type": "module"`），因为该包只导出 `import` 入口，其 README 明确「CommonJS `require()` is not an advertised entry point」。
除此之外不引入框架：HTTP 服务用 `node:http`，界面为原生 HTML/CSS/JS。

## 从安装到启动

以下命令都在**交付目录内**执行：发布分支 `2026-competition-ecosystem-qoder` 根下的 `qoder/`（本目录）。
本地验收使用 `pnpm`，依赖版本已锁定在 `pnpm-lock.yaml`。也可用 `npm install`、`npm start` 和 `npm test`。

Windows（PowerShell，验收环境）：

```powershell
cd qoder
pnpm install        # seekdb-node 1.4.0（含平台原生运行时）+ mysql2 3.11.5
pnpm start          # 浏览器打开 http://127.0.0.1:4321
pnpm test           # 主流程验收 + 生成 docs/seekdb-evidence.md
```

Linux / macOS：

```bash
cd qoder
pnpm install                # 或 npm install
pnpm start
```

端口与数据目录（默认写到项目内 `.data/seekdb`，已在 `.gitignore` 中）：

```bash
EXPDB_PORT=5111 EXPDB_HOME=/tmp/expdb pnpm start                    # Linux/macOS
$env:EXPDB_PORT=5111; $env:EXPDB_HOME='D:\tmp\expdb'; pnpm start     # PowerShell
pnpm start -- --data "D:\tmp\expdb"                                  # 也支持 --data
```

`memory_budget` 默认 `2G`（实机确认 `256M` 启动失败），可用 `SEEKDB_MEMORY_BUDGET` 覆盖；连接池大小用 `EXPDB_POOL_SIZE` 覆盖。
首次启动自动导入 `samples/` 的 4 份演示记录；已有库再启动只会提示指纹相同、不产生副本。
`pnpm run seed` 手动播种（可重复执行），`pnpm run explain 复现实验` 打印真实检索 SQL、传参与 EXPLAIN。

## 一分钟验收（评审用）

1. `pnpm start` → 打开 `http://127.0.0.1:4321`，状态条显示 seekdb 连接通道、`FULLTEXT ik`、记录数与 `utf8mb4`；鼠标悬停可看索引 DDL 与服务端版本。
2. **导入 + 去重**：把 `samples/02-改进-tcn.md` 粘进「导入实验记录」→「导入并解析」，返回项目/数据集/划分与指标数；
   再点「再导入 samples/02-改进-tcn.md」→ 提示*重复导入已识别*（内容指纹相同，未新增副本）。
3. **检索 + 证据**：输入 `学习率` 回车 → 证据卡片列出命中文段、原记录名与行号；
   点「查看检索 SQL / EXPLAIN」看到参数化的 `MATCH(body) AGAINST (?)` 与实际执行计划（算子 `TEXT RETRIEVAL SCAN`）。
   现场粘贴一份含中文短语 `复现实验` 与英文专有词 `RareKettle7` 的记录，导入后两个词都能立刻命中；
   把「项目」筛选换成另一个项目，该命中消失；输入空查询或不存在的词，得到明确提示而不是空白。
4. **对比**：选 `手工基线` 与 `改进 TCN` → accuracy 71.4% → 78.9%，增加 7.5 个百分点（相对 +10.5%），并各自显示数据集与划分；
   换成 `改进 TCN` 与 `睡眠分期 LSTM` → 提示数据集不同、accuracy 单位不一致（`%` vs 比例），判定不可直接比较。
   点「复制 Markdown 摘要」，粘贴文本含每条指标的证据行号与两条记录的指纹前缀。
5. **持久化**：Ctrl+C 停服务再 `pnpm start`，搜索与对比照常；或直接 `pnpm test`——一次临时 seekdb 实例跑完
   26 项断言（导入提取、指纹去重、并发同内容只落一条、事务回滚、中英文索引命中、查询词元 `TOKENIZE(...,'ik')`、
   `TEXT RETRIEVAL SCAN`、筛选收窄且 SQL 无用户输入拼接、空查询与失配提示、可比/不可比、导出摘要、重启后仍可检索），
   并写入 `docs/seekdb-evidence.md`。

## seekdb 接入要点

```js
import { seekdb } from '@oceanbase/seekdb-node';      // ESM 命名导出，open 是类静态方法
const db = await seekdb.open(dataDir, {
  parameters: { memory_budget: '2G', mysql_port_mode: 'random' },   // 参数值必须是字符串
});
const ep = db.connectionOptions();   // 判别式联合，含 user 字段
// { transport: 'tcp', user, host, port } | { transport: 'unix_socket', user, unixSocket } | { transport: 'named_pipe', user, namedPipe }
const pool = mysql.createPool({ ...toMysqlLocation(ep), user: ep.user, password: '', database: 'test', connectionLimit: 4 });
// … SQL …
await pool.end(); db.close();        // close() 同步；先关 SQL 客户端再关句柄
```

- `open()` 启动/复用的是**独立 seekdb 服务端进程**（不是进程内 SQL 引擎），SQL 连接由应用自己持有；同一目录再次 `open()` 保留数据，这是「重启后记录仍可检索」的实现基础。
- 连接初始化任一步失败都会 `native.close()`，不留孤立服务进程（`src/db.js:open` 的 try/catch）。
- `toMysqlLocation()` 根据 seekdb 返回的端点使用 TCP、Unix socket 或命名管道。Windows 验收走 `127.0.0.1` 随机 TCP 端口。命名管道分支为每条连接创建独立的流。
- 普通参数（如 `memory_budget`）只在目录**首次初始化**时生效并由 seekdb 持久化；因此每次验收都从新目录（临时目录）开始，取值才与代码一致。
- `utf8mb4` 表用 `FULLTEXT ... WITH PARSER ik` 建索引，参数化的 `MATCH(body) AGAINST (?)` 同时命中中文短语和英文专有词。`EXPLAIN` 显示 `TEXT RETRIEVAL SCAN`。
- ik 会把 `复现实验` 切成 `复现` + `实验`（实机 `SELECT TOKENIZE('复现实验','ik')` 取证），因此自然语言模式可能按**部分词元**命中：
  正文中的「本次复现与原始结果一致」也会命中。证据卡片按原词和实际词元定位行号。
- SQL 身份是 `root@127.0.0.1`、空密码（本机 seekdb 内核的默认账号，`root` 只对 `127.0.0.1` 生效）；HTTP 服务同样只绑 `127.0.0.1`，不对外监听、不采集任何数据。

## 数据落在哪里

- `exp_record`：正文 `body`（MEDIUMTEXT, utf8mb4）+ 内容指纹唯一索引 + 项目/数据集/划分等元信息 + `FULLTEXT ft_exp_body(body) WITH PARSER ik`。
- `exp_metric`：指标名、归一化 key、数值、单位、原文片段与 `line_no`（对比表与摘要的行号来源）。
- `bench_meta`：记录实际生效的分词器（`ft_parser`），重启后不重复建索引。
- 写库只在本机 seekdb 目录；`body` 存原文，行号按原文行序还原，可回到 `.md` 逐行复核。

## 测试

```bash
pnpm test              # 临时目录起一个 seekdb，执行 26 项断言并生成取证文件
pnpm run evidence      # 只生成 docs/seekdb-evidence.md（真实 SQL + 传参 + EXPLAIN 原文）
pnpm run explain 复现实验 # 打印检索 SQL / 传参 / EXPLAIN，并列出该查询的 ik 词元
pnpm run seed          # 幂等导入演示样例
```

## 展示与验收材料

`site/` 是面向评审的 Qoder Sites 静态页面，使用浏览器内的虚构样例展示交互。导入、词元检索与持久化验收在本地的 seekdb 上运行。

- [seekdb 实测结果](docs/seekdb-evidence.md)记录索引定义、参数、命中行和执行计划。
- [检索证据](site/screenshots/search-evidence.png)和[对比提示](site/screenshots/compare-warning.png)截自本地实际运行界面。
- [Qoder 开发记录](docs/qoder-dev-record.md)附已去除本机路径的实际工具事件和截图。

## 目录

```
qoder/
├─ src/        db / parse / store / search / compare / server / cli / config
├─ public/     index.html + app.js + styles.css（中文界面，键盘可操作，手机可用）
├─ samples/    4 份虚构演示记录（3 个项目、3 个数据集）
├─ test/       e2e.js，全部 26 项验收断言
├─ docs/       seekdb 实测证据和 Qoder 开发记录
├─ site/       Qoder Sites 静态项目主页（见 site/README.md）
├─ package.json / pnpm-lock.yaml
└─ .gitignore  屏蔽 node_modules/.data/日志
```
