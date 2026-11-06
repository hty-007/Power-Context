# PowerContext 2026 参赛与公开自测

从本分支的产品代码开始实现题目。代码基线为 `9db1753434162c0aa7c9f1189a1722e617880a52`（PowerContext v1.2.0）；`competition/` 提供题面、公开样例和公开行为测试。实现仍放在产品的 `src/` 中。

## 选择题目

| 赛题 | 分值 | 题面 | 本地自测 |
| --- | ---: | --- | --- |
| 一：准备 PowerContext 参赛仓库 | 10 | [仓库准备](challenges/setup-00-repository-fork/instruction.md) | `make check-baseline` |
| 二：CSV 行级采集与原始记录回查 | 20 | [CSV](challenges/source-01-table-evidence/instruction.md) | `make test-csv` |
| 三：Pi 会话分支采集与断点恢复 | 20 | [Pi](challenges/source-02-pi-history/instruction.md) | `make test-pi` |
| 四：财报 PDF 全文解析与证据页检索 | 50 | [PDF](challenges/source-03-pdf-retrieval/instruction.md) | `make test-pdf` |
| 五：HTTP 重排服务接入与跨批次排序 | 20 | [HTTP 重排](challenges/retrieval-01-reranking/instruction.md) | `make test-rerank` |
| 六：上下文增量传输与完整重建 | 20 | [增量上下文](challenges/context-02-incremental-pack/instruction.md) | `make test-incremental` |
| 七：按新增来源条目数触发记忆处理 | 20 | [数量触发](challenges/processing-01-count-trigger/instruction.md) | `make test-count-trigger` |
| 八：Embedding 并发请求合并与向量缓存 | 20 | [Embedding](challenges/memory-02-embedding-batches/instruction.md) | `make test-embedding` |
| 九：来源撤回后的记忆停用与恢复 | 20 | [来源生命周期](challenges/memory-01-source-lifecycle/instruction.md) | `make test-lifecycle` |
| 十：按查询和预算选择原文证据 | 20 | [证据选择](challenges/context-01-evidence-selection/instruction.md) | `make test-evidence` |
| 十一：seekdb 混合检索的单 SQL 候选查询 | 20 | [混合检索](challenges/retrieval-02-hybrid-search/instruction.md) | `make test-hybrid` |
| 合计 | **240** | | |

表中的命令在 `competition/` 运行。每道代码题按完整验收是否通过计分。本地命令只给出公开测试结果，不生成或上报比赛成绩，公开测试通过也不代表已经满足题面全部质量与性能要求。

## 在本机运行公开测试

环境要求为 Linux x86_64、Python 3.12 和 uv。所有测试依赖固定在 [requirements.txt](requirements.txt)，不需要模型 API Key、平台 Token。seekdb 测试使用嵌入式 pylibseekdb，无需另行部署数据库服务。

从产品仓库根目录执行：

```bash
make -C competition install
make -C competition check-baseline
make -C competition test-csv
```

安装依赖需要网络；测试使用本地样例、固定响应和临时数据库，不调用在线模型。数量触发题会运行本地 HTTP 测试服务，并包含等待与恢复场景，执行时间较长，期间不要中断进程。

运行全部公开代码题测试，各题使用独立 Python 进程，避免嵌入式数据库的进程内状态相互影响；某题失败后仍继续运行其余题，最后返回非零状态：

```bash
make -C competition test
```

如需对另一个产品 checkout 运行测试，只切换产品目录：

```bash
make -C competition test-pi PRODUCT=/absolute/path/to/product
```

测试从该目录的 `src/` 加载实现，不从已安装的其他 PowerContext 版本加载。pytest 输出 `FAILED` 或导入错误都表示本轮自测未通过；基线尚未实现的接口出现导入错误属于预期起点。不要通过跳过测试或改动测试文件消除失败。

## 使用统一 Docker 环境

宿主不便准备 Python 依赖时，在安装 Docker 与 Compose 后执行：

```bash
make -C competition docker-build
make -C competition docker-test
```

构建阶段下载固定依赖，运行阶段关闭外部网络。启动脚本为嵌入式 seekdb 配置仅容器内可用的虚拟地址，随后切换为普通用户执行测试，不连接外部网络。容器配额为 1 CPU、2 GiB，产品源码和公开测试均只读挂载，数据库及临时文件写入容器临时目录。Docker 环境支持 Linux x86_64；跨架构模拟的耗时不能用于判断性能是否达标。

只运行某一题也可直接使用 pytest：

```bash
cd competition
docker compose run --rm selftest python -m pytest -q \
  -c /selftest/pytest.ini --confcutdir=/selftest \
  /selftest/challenges/source-01-table-evidence/acceptance
```

## 准备提测

按参赛指南导入 PowerContext 并保留提交历史，私有仓库须授予平台读取权限；已有公开 Fork 也可使用。仓库准备题接受任一分支包含冻结基线，不要求新建特定名称的分支。`check-baseline` 只检查本地历史，不证明 GitHub 权限或仓库归属，也不覆盖官方 Fork 关联这一替代条件。

一次提测可以包含全部代码题的实现。提交实际仓库、分支及 commit；未指定版本时，平台将使用默认分支的实际版本。提交前确认默认分支包含自己的实现。代码题只加载 `src/`，不能通过修改本目录的测试、数据或依赖改变评测结果。

公开包中的 PDF、CSV、SciFact 和会话数据保留各自来源、归属和许可，见对应 `workload/` 文件。样例中的代码、链接及历史命令只作为输入文字，不需要执行。
