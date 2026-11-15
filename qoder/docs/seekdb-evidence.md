# seekdb 检索取证（由 npm test 真实运行生成）

- 生成时间：2026-11-15T15:09:01.633Z · 平台 win32 · Node v24.22.1
- @oceanbase/seekdb-node 1.4.0 · 服务端 `5.7.25-OceanBase seekdb-v1.4.0.0` · 连接通道 tcp 127.0.0.1:51304（connectionOptions() 判别式）· 库 test · utf8mb4
- 索引 DDL：`CREATE FULLTEXT INDEX ft_exp_body ON exp_record(body) WITH PARSER ik` · 解析器 `ik`
- 检索方式：自然语言模式 `MATCH(body) AGAINST (?)`，EXPLAIN 使用同一条 SQL 与同一组参数
- memory_budget：`2G`
- 主流程断言：26 通过，0 失败

## 中文短语 复现实验
- 模式：fulltext · 命中：#5 复现实验：雾钟 RareKettle7 阈值扫描（score 0.3473，行 1/11）；#2 改进：TCN 时序卷积网络（score 0.1143，行 3/28）；#4 跨集迁移：TCN 在院前转运数据上（score 0.0534，行 3）；#3 睡眠呼吸事件分期：LSTM 基线（score 0.0520，行 3）；#1 基线：手工特征 + Logistic 回归（score 0.0458，行 3）
- 该查询的 ik 词元（`SELECT TOKENIZE(?, 'ik')`）：["实验","复现"]
- 证据行定位词（原词 + 词元）：["复现实验","实验","复现"]
- 界面提示：证据行按 ik 实际词元定位：实验 / 复现（自然语言模式可能只按部分词元命中）。
执行的 SQL（占位符即实际语句，不含用户输入原文）：
```sql
SELECT id, name, project, dataset, split, source, body, line_count,
       MATCH(body) AGAINST (?) AS score
  FROM exp_record
 WHERE MATCH(body) AGAINST (?)
 ORDER BY score DESC, id ASC
 LIMIT 20
```
- 传参：`["复现实验","复现实验"]`
EXPLAIN 原文：
```json
[
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "|ID|OPERATOR             |NAME                   |EST.ROWS|EST.TIME(us)|"
  },
  {
    "Query Plan": "------------------------------------------------------------------------"
  },
  {
    "Query Plan": "|0 |TOP-N SORT           |                       |7       |237         |"
  },
  {
    "Query Plan": "|1 |└─TEXT RETRIEVAL SCAN|exp_record(ft_exp_body)|7       |231         |"
  },
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "Outputs & filters:"
  },
  {
    "Query Plan": "-------------------------------------"
  },
  {
    "Query Plan": "  0 - output([exp_record.id], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source], [exp_record.body],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('复现实验')]), filter(nil), rowset=16"
  },
  {
    "Query Plan": "      sort_keys([MATCH(exp_record.body) AGAINST('复现实验'), DESC], [exp_record.id, ASC]), topn(20)"
  },
  {
    "Query Plan": "  1 - output([exp_record.id], [exp_record.body], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('复现实验')]), filter(nil), rowset=16"
  },
  {
    "Query Plan": "      access([exp_record.id], [exp_record.body], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count]), partitions(p0)"
  },
  {
    "Query Plan": "      is_index_back=true, is_global_index=false, "
  },
  {
    "Query Plan": "      calc_relevance=true, match_expr(MATCH(exp_record.body) AGAINST('复现实验')), "
  },
  {
    "Query Plan": "      pushdown_match_filter(MATCH(exp_record.body) AGAINST('复现实验')), "
  },
  {
    "Query Plan": "      sort_keys([MATCH(exp_record.body) AGAINST('复现实验'), DESC]), limit(20), offset(nil), with_ties(true)"
  }
]
```
## 英文专有词 RareKettle7
- 模式：fulltext · 命中：#5 复现实验：雾钟 RareKettle7 阈值扫描（score 0.6506，行 1/11）
- 该查询的 ik 词元（`SELECT TOKENIZE(?, 'ik')`）：["rarekettle7"]
- 证据行定位词（原词 + 词元）：["rarekettle7"]
执行的 SQL（占位符即实际语句，不含用户输入原文）：
```sql
SELECT id, name, project, dataset, split, source, body, line_count,
       MATCH(body) AGAINST (?) AS score
  FROM exp_record
 WHERE MATCH(body) AGAINST (?)
 ORDER BY score DESC, id ASC
 LIMIT 20
```
- 传参：`["RareKettle7","RareKettle7"]`
EXPLAIN 原文：
```json
[
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "|ID|OPERATOR             |NAME                   |EST.ROWS|EST.TIME(us)|"
  },
  {
    "Query Plan": "------------------------------------------------------------------------"
  },
  {
    "Query Plan": "|0 |TOP-N SORT           |                       |1       |42          |"
  },
  {
    "Query Plan": "|1 |└─TEXT RETRIEVAL SCAN|exp_record(ft_exp_body)|1       |41          |"
  },
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "Outputs & filters:"
  },
  {
    "Query Plan": "-------------------------------------"
  },
  {
    "Query Plan": "  0 - output([exp_record.id], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source], [exp_record.body],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('RareKettle7')]), filter(nil), rowset=16"
  },
  {
    "Query Plan": "      sort_keys([MATCH(exp_record.body) AGAINST('RareKettle7'), DESC], [exp_record.id, ASC]), topn(20)"
  },
  {
    "Query Plan": "  1 - output([exp_record.id], [exp_record.body], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('RareKettle7')]), filter(nil), rowset=16"
  },
  {
    "Query Plan": "      access([exp_record.id], [exp_record.body], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count]), partitions(p0)"
  },
  {
    "Query Plan": "      is_index_back=true, is_global_index=false, "
  },
  {
    "Query Plan": "      calc_relevance=true, match_expr(MATCH(exp_record.body) AGAINST('RareKettle7')), "
  },
  {
    "Query Plan": "      pushdown_match_filter(MATCH(exp_record.body) AGAINST('RareKettle7')), "
  },
  {
    "Query Plan": "      sort_keys([MATCH(exp_record.body) AGAINST('RareKettle7'), DESC]), limit(20), offset(nil), with_ties(true)"
  }
]
```
## 样例中文词 学习率
- 模式：fulltext · 命中：#2 改进：TCN 时序卷积网络（score 0.2100，行 13/27）；#4 跨集迁移：TCN 在院前转运数据上（score 0.1253，行 13）；#1 基线：手工特征 + Logistic 回归（score 0.0458，行 13）
- 该查询的 ik 词元（`SELECT TOKENIZE(?, 'ik')`）：["学习","率"]
- 证据行定位词（原词 + 词元）：["学习率","学习","率"]
- 界面提示：证据行按 ik 实际词元定位：学习 / 率（自然语言模式可能只按部分词元命中）。
执行的 SQL（占位符即实际语句，不含用户输入原文）：
```sql
SELECT id, name, project, dataset, split, source, body, line_count,
       MATCH(body) AGAINST (?) AS score
  FROM exp_record
 WHERE MATCH(body) AGAINST (?)
 ORDER BY score DESC, id ASC
 LIMIT 20
```
- 传参：`["学习率","学习率"]`
EXPLAIN 原文：
```json
[
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "|ID|OPERATOR             |NAME                   |EST.ROWS|EST.TIME(us)|"
  },
  {
    "Query Plan": "------------------------------------------------------------------------"
  },
  {
    "Query Plan": "|0 |TOP-N SORT           |                       |5       |176         |"
  },
  {
    "Query Plan": "|1 |└─TEXT RETRIEVAL SCAN|exp_record(ft_exp_body)|5       |171         |"
  },
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "Outputs & filters:"
  },
  {
    "Query Plan": "-------------------------------------"
  },
  {
    "Query Plan": "  0 - output([exp_record.id], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source], [exp_record.body],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('学习率')]), filter(nil), rowset=16"
  },
  {
    "Query Plan": "      sort_keys([MATCH(exp_record.body) AGAINST('学习率'), DESC], [exp_record.id, ASC]), topn(20)"
  },
  {
    "Query Plan": "  1 - output([exp_record.id], [exp_record.body], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('学习率')]), filter(nil), rowset=16"
  },
  {
    "Query Plan": "      access([exp_record.id], [exp_record.body], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count]), partitions(p0)"
  },
  {
    "Query Plan": "      is_index_back=true, is_global_index=false, "
  },
  {
    "Query Plan": "      calc_relevance=true, match_expr(MATCH(exp_record.body) AGAINST('学习率')), "
  },
  {
    "Query Plan": "      pushdown_match_filter(MATCH(exp_record.body) AGAINST('学习率')), "
  },
  {
    "Query Plan": "      sort_keys([MATCH(exp_record.body) AGAINST('学习率'), DESC]), limit(20), offset(nil), with_ties(true)"
  }
]
```
## 失配词
- 模式：miss · 命中：无
- 界面提示：索引未命中：请换成与记录原文一致的写法（中英文别名分开各试一次），或去掉项目/数据集筛选后重试。
执行的 SQL（占位符即实际语句，不含用户输入原文）：
```sql
SELECT id, name, project, dataset, split, source, body, line_count,
       MATCH(body) AGAINST (?) AS score
  FROM exp_record
 WHERE MATCH(body) AGAINST (?)
 ORDER BY score DESC, id ASC
 LIMIT 20
```
- 传参：`["zzqxnorarevphft","zzqxnorarevphft"]`
EXPLAIN 原文：
```json
[
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "|ID|OPERATOR             |NAME                   |EST.ROWS|EST.TIME(us)|"
  },
  {
    "Query Plan": "------------------------------------------------------------------------"
  },
  {
    "Query Plan": "|0 |TOP-N SORT           |                       |1       |42          |"
  },
  {
    "Query Plan": "|1 |└─TEXT RETRIEVAL SCAN|exp_record(ft_exp_body)|1       |41          |"
  },
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "Outputs & filters:"
  },
  {
    "Query Plan": "-------------------------------------"
  },
  {
    "Query Plan": "  0 - output([exp_record.id], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source], [exp_record.body],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('zzqxnorarevphft')]), filter(nil), rowset=16"
  },
  {
    "Query Plan": "      sort_keys([MATCH(exp_record.body) AGAINST('zzqxnorarevphft'), DESC], [exp_record.id, ASC]), topn(20)"
  },
  {
    "Query Plan": "  1 - output([exp_record.id], [exp_record.body], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('zzqxnorarevphft')]), filter(nil), rowset=16"
  },
  {
    "Query Plan": "      access([exp_record.id], [exp_record.body], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count]), partitions(p0)"
  },
  {
    "Query Plan": "      is_index_back=true, is_global_index=false, "
  },
  {
    "Query Plan": "      calc_relevance=true, match_expr(MATCH(exp_record.body) AGAINST('zzqxnorarevphft')), "
  },
  {
    "Query Plan": "      pushdown_match_filter(MATCH(exp_record.body) AGAINST('zzqxnorarevphft')), "
  },
  {
    "Query Plan": "      sort_keys([MATCH(exp_record.body) AGAINST('zzqxnorarevphft'), DESC]), limit(20), offset(nil), with_ties(true)"
  }
]
```
## 复现实验 + 项目=院前呼吸衰竭转运
- 模式：fulltext · 命中：#4 跨集迁移：TCN 在院前转运数据上（score 0.0534，行 3）
- 该查询的 ik 词元（`SELECT TOKENIZE(?, 'ik')`）：["实验","复现"]
- 证据行定位词（原词 + 词元）：["复现实验","实验","复现"]
- 界面提示：证据行按 ik 实际词元定位：实验 / 复现（自然语言模式可能只按部分词元命中）。
执行的 SQL（占位符即实际语句，不含用户输入原文）：
```sql
SELECT id, name, project, dataset, split, source, body, line_count,
       MATCH(body) AGAINST (?) AS score
  FROM exp_record
 WHERE MATCH(body) AGAINST (?) AND project = ?
 ORDER BY score DESC, id ASC
 LIMIT 20
```
- 传参：`["复现实验","复现实验","院前呼吸衰竭转运"]`
EXPLAIN 原文：
```json
[
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "|ID|OPERATOR             |NAME                   |EST.ROWS|EST.TIME(us)|"
  },
  {
    "Query Plan": "------------------------------------------------------------------------"
  },
  {
    "Query Plan": "|0 |TOP-N SORT           |                       |2       |92          |"
  },
  {
    "Query Plan": "|1 |└─TEXT RETRIEVAL SCAN|exp_record(ft_exp_body)|2       |91          |"
  },
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "Outputs & filters:"
  },
  {
    "Query Plan": "-------------------------------------"
  },
  {
    "Query Plan": "  0 - output([exp_record.id], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source], [exp_record.body],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('复现实验')]), filter(nil), rowset=16"
  },
  {
    "Query Plan": "      sort_keys([MATCH(exp_record.body) AGAINST('复现实验'), DESC], [exp_record.id, ASC]), topn(20)"
  },
  {
    "Query Plan": "  1 - output([exp_record.id], [exp_record.body], [exp_record.project], [exp_record.name], [exp_record.dataset], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('复现实验')]), filter([exp_record.project = '院前呼吸衰竭转运']), rowset=16"
  },
  {
    "Query Plan": "      access([exp_record.id], [exp_record.body], [exp_record.project], [exp_record.name], [exp_record.dataset], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count]), partitions(p0)"
  },
  {
    "Query Plan": "      is_index_back=true, is_global_index=false, filter_before_indexback[false], "
  },
  {
    "Query Plan": "      calc_relevance=true, match_expr(MATCH(exp_record.body) AGAINST('复现实验')), "
  },
  {
    "Query Plan": "      pushdown_match_filter(MATCH(exp_record.body) AGAINST('复现实验'))"
  }
]
```
## accuracy + 数据集=SleepEDF-20
- 模式：fulltext · 命中：#3 睡眠呼吸事件分期：LSTM 基线（score 0.0951，行 19/26）
- 该查询的 ik 词元（`SELECT TOKENIZE(?, 'ik')`）：["accuracy"]
- 证据行定位词（原词 + 词元）：["accuracy"]
执行的 SQL（占位符即实际语句，不含用户输入原文）：
```sql
SELECT id, name, project, dataset, split, source, body, line_count,
       MATCH(body) AGAINST (?) AS score
  FROM exp_record
 WHERE MATCH(body) AGAINST (?) AND dataset = ?
 ORDER BY score DESC, id ASC
 LIMIT 20
```
- 传参：`["accuracy","accuracy","SleepEDF-20"]`
EXPLAIN 原文：
```json
[
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "|ID|OPERATOR             |NAME                   |EST.ROWS|EST.TIME(us)|"
  },
  {
    "Query Plan": "------------------------------------------------------------------------"
  },
  {
    "Query Plan": "|0 |TOP-N SORT           |                       |1       |61          |"
  },
  {
    "Query Plan": "|1 |└─TEXT RETRIEVAL SCAN|exp_record(ft_exp_body)|1       |60          |"
  },
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "Outputs & filters:"
  },
  {
    "Query Plan": "-------------------------------------"
  },
  {
    "Query Plan": "  0 - output([exp_record.id], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source], [exp_record.body],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('accuracy')]), filter(nil), rowset=16"
  },
  {
    "Query Plan": "      sort_keys([MATCH(exp_record.body) AGAINST('accuracy'), DESC], [exp_record.id, ASC]), topn(20)"
  },
  {
    "Query Plan": "  1 - output([exp_record.id], [exp_record.body], [exp_record.dataset], [exp_record.name], [exp_record.project], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('accuracy')]), filter([exp_record.dataset = 'SleepEDF-20']), rowset=16"
  },
  {
    "Query Plan": "      access([exp_record.id], [exp_record.body], [exp_record.dataset], [exp_record.name], [exp_record.project], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count]), partitions(p0)"
  },
  {
    "Query Plan": "      is_index_back=true, is_global_index=false, filter_before_indexback[false], "
  },
  {
    "Query Plan": "      calc_relevance=true, match_expr(MATCH(exp_record.body) AGAINST('accuracy')), "
  },
  {
    "Query Plan": "      pushdown_match_filter(MATCH(exp_record.body) AGAINST('accuracy'))"
  }
]
```
## 重启后 复现实验
- 模式：fulltext · 命中：#5 复现实验：雾钟 RareKettle7 阈值扫描（score 0.3473，行 1/11）；#2 改进：TCN 时序卷积网络（score 0.1143，行 3/28）；#4 跨集迁移：TCN 在院前转运数据上（score 0.0534，行 3）；#3 睡眠呼吸事件分期：LSTM 基线（score 0.0520，行 3）；#1 基线：手工特征 + Logistic 回归（score 0.0458，行 3）
- 该查询的 ik 词元（`SELECT TOKENIZE(?, 'ik')`）：["实验","复现"]
- 证据行定位词（原词 + 词元）：["复现实验","实验","复现"]
- 界面提示：证据行按 ik 实际词元定位：实验 / 复现（自然语言模式可能只按部分词元命中）。
执行的 SQL（占位符即实际语句，不含用户输入原文）：
```sql
SELECT id, name, project, dataset, split, source, body, line_count,
       MATCH(body) AGAINST (?) AS score
  FROM exp_record
 WHERE MATCH(body) AGAINST (?)
 ORDER BY score DESC, id ASC
 LIMIT 20
```
- 传参：`["复现实验","复现实验"]`
EXPLAIN 原文：
```json
[
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "|ID|OPERATOR             |NAME                   |EST.ROWS|EST.TIME(us)|"
  },
  {
    "Query Plan": "------------------------------------------------------------------------"
  },
  {
    "Query Plan": "|0 |TOP-N SORT           |                       |7       |237         |"
  },
  {
    "Query Plan": "|1 |└─TEXT RETRIEVAL SCAN|exp_record(ft_exp_body)|7       |231         |"
  },
  {
    "Query Plan": "========================================================================"
  },
  {
    "Query Plan": "Outputs & filters:"
  },
  {
    "Query Plan": "-------------------------------------"
  },
  {
    "Query Plan": "  0 - output([exp_record.id], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source], [exp_record.body],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('复现实验')]), filter(nil), rowset=16"
  },
  {
    "Query Plan": "      sort_keys([MATCH(exp_record.body) AGAINST('复现实验'), DESC], [exp_record.id, ASC]), topn(20)"
  },
  {
    "Query Plan": "  1 - output([exp_record.id], [exp_record.body], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count], [MATCH(exp_record.body) AGAINST('复现实验')]), filter(nil), rowset=16"
  },
  {
    "Query Plan": "      access([exp_record.id], [exp_record.body], [exp_record.name], [exp_record.project], [exp_record.dataset], [exp_record.split], [exp_record.source],"
  },
  {
    "Query Plan": "       [exp_record.line_count]), partitions(p0)"
  },
  {
    "Query Plan": "      is_index_back=true, is_global_index=false, "
  },
  {
    "Query Plan": "      calc_relevance=true, match_expr(MATCH(exp_record.body) AGAINST('复现实验')), "
  },
  {
    "Query Plan": "      pushdown_match_filter(MATCH(exp_record.body) AGAINST('复现实验')), "
  },
  {
    "Query Plan": "      sort_keys([MATCH(exp_record.body) AGAINST('复现实验'), DESC]), limit(20), offset(nil), with_ties(true)"
  }
]
```
