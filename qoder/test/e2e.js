// 主流程验收：一次临时 seekdb 实例，跑完导入 → 去重/事务 → FULLTEXT 检索与筛选 → 可比性 → 重启持久化
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { cfg } from '../src/config.js';
import * as db from '../src/db.js';
import * as store from '../src/store.js';
import * as searcher from '../src/search.js';
import * as cmp from '../src/compare.js';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const SAMPLES = path.join(ROOT, 'samples');
let pass = 0;
let fail = 0;
const evidence = [];

function ok(name, cond, detail = '') {
  if (cond) { pass++; console.log(`  ✓ ${name}${detail ? ' — ' + detail : ''}`); }
  else { fail++; console.log(`  ✗ ${name}${detail ? ' — ' + detail : ''}`); }
}
const log = (label, res) => evidence.push({ label, mode: res.meta.mode, sql: res.meta.sql, params: res.meta.params, tokens: res.meta.tokens, terms: res.meta.terms, tokenizeError: res.meta.tokenizeError, explain: res.meta.explain, explainError: res.meta.explainError, note: res.meta.note, hits: res.hits.map((h) => ({ id: h.id, name: h.name, score: h.score, lines: h.evidence.map((e) => e.line) })) });
const planText = (x) => JSON.stringify(x ?? null);

// 现场导入的新记录：中文短语 + 无分隔英文专有词，用来证明索引对新写入即刻生效
const FRESH_MD = `# 复现实验：雾钟 RareKettle7 阈值扫描

> 演示数据：内容为虚构样例，仅用于验证检索链路。

- 项目：慢阻肺急性加重风险
- 数据集：COPD-HR-v3
- 划分：5折交叉验证

## 设置

这是一次复现实验，用于检验 RareKettle7 阈值扫描能否被 seekdb 全文索引命中。

## 指标

| 指标 | 数值 | 单位 |
| --- | --- | --- |
| f1 | 0.66 |  |
| accuracy | 75.5 | % |
`;

async function seedAll(client) {
  const ids = {};
  for (const f of fs.readdirSync(SAMPLES).filter((x) => x.endsWith('.md')).sort()) {
    const r = await store.importRecord(client, fs.readFileSync(path.join(SAMPLES, f), 'utf8'), `samples/${f}`);
    ids[f.slice(0, 2)] = { id: r.id, duplicate: r.duplicate };
  }
  return ids;
}

async function run() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'expbench-'));
  let handle = await db.open({ dataDir: dir, memoryBudget: cfg.memoryBudget, database: cfg.database, poolSize: cfg.poolSize });
  let schema = await db.initSchema(handle.pool);
  const [[ver]] = await handle.pool.query('SELECT VERSION() AS v');
  const transport = handle.transport;
  console.log(`seekdb ${ver.v} · ${transport} · 库 ${cfg.database} · utf8mb4`);
  console.log(`FULLTEXT：${schema.indexDdl}${schema.ftError ? `（${schema.ftError} → 已退回默认解析器）` : ''}`);

  console.log('\n[1] 导入多项目多数据集样例');
  const ids = await seedAll(handle.pool);
  const st = await store.stats(handle.pool);
  ok('4 份演示样例入库', st.records === 4, `records=${st.records} metrics=${st.metrics}`);
  const r1 = await store.getRecord(handle.pool, ids['01'].id);
  ok('提取项目/数据集/划分', r1.project === '慢阻肺急性加重风险' && r1.dataset === 'COPD-HR-v3' && r1.split === '5折交叉验证', `${r1.project} | ${r1.dataset} | ${r1.split}`);
  ok('提取核心指标', r1.metrics.length >= 5, `${r1.metrics.length} 项，accuracy=${r1.metrics.find((m) => m.mkey === 'accuracy')?.value}${r1.metrics.find((m) => m.mkey === 'accuracy')?.unit}`);

  console.log('\n[2] 内容指纹去重 + 导入事务完整性');
  const dup = await store.importRecord(handle.pool, fs.readFileSync(path.join(SAMPLES, '01-手工基线-logistic.md'), 'utf8'), 'again');
  ok('重复导入返回已有记录', dup.duplicate === true && dup.id === ids['01'].id, `命中记录 #${dup.id}`);
  ok('库内记录数不变', (await store.stats(handle.pool)).records === 4);
  const concurrent = await Promise.all([1, 2, 3, 4].map(() => store.importRecord(handle.pool, FRESH_MD, '并发导入')));
  const [rr] = await handle.pool.execute('SELECT COUNT(*) AS c FROM exp_record WHERE name = ?', ['复现实验：雾钟 RareKettle7 阈值扫描']);
  ok('并发同内容导入只落一条', Number(rr[0].c) === 1 && new Set(concurrent.map((x) => x.id)).size === 1, `并发 ${concurrent.length} 次 → ${rr[0].c} 条，id=${[...new Set(concurrent.map((x) => x.id))].join(',')}`);
  const freshId = concurrent[0].id;
  const before = (await store.stats(handle.pool)).records;
  let threw = false;
  try {
    await store.withTransaction(handle.pool, async (conn) => {
      await conn.execute(
        `INSERT INTO exp_record (fingerprint, name, project, dataset, split, body, line_count) VALUES (?, ?, ?, ?, ?, ?, ?)`,
        ['t'.repeat(64), '模拟失败记录', 'p', 'd', 's', 'x', 1]
      );
      throw new Error('模拟指标写入失败');
    });
  } catch { threw = true; }
  const [[orph]] = await handle.pool.execute('SELECT COUNT(*) AS c FROM exp_record WHERE fingerprint = ?', ['t'.repeat(64)]);
  ok('事务失败整体回滚，不留缺指标记录', threw && Number(orph.c) === 0 && (await store.stats(handle.pool)).records === before, `回滚前 ${before} 条 → 现在 ${(await store.stats(handle.pool)).records} 条`);

  console.log('\n[3] FULLTEXT（ik + 自然语言模式）命中中英文');
  const zh = await searcher.search(handle.pool, { q: '复现实验' });
  log('中文短语 复现实验', zh);
  ok('新导入的中文记录走索引命中', zh.meta.mode === 'fulltext' && zh.hits.some((h) => h.id === freshId), `模式 ${zh.meta.mode}，命中 ${zh.hits.length} 条`);
  const en = await searcher.search(handle.pool, { q: 'RareKettle7' });
  log('英文专有词 RareKettle7', en);
  ok('新导入的英文专有词走索引命中', en.meta.mode === 'fulltext' && en.hits.some((h) => h.id === freshId), `模式 ${en.meta.mode}，命中 ${en.hits.length} 条`);
  ok('执行计划为 TEXT RETRIEVAL SCAN', /TEXT RETRIEVAL SCAN/.test(planText(zh.meta.explain)), planText(zh.meta.explain).slice(0, 160) || zh.meta.explainError);
  const sample = await searcher.search(handle.pool, { q: '学习率' });
  log('样例中文词 学习率', sample);
  ok('样例正文的中文用词同样命中', sample.meta.mode === 'fulltext' && sample.hits.length >= 2, `命中 ${sample.hits.length} 条`);
  ok('证据行号落在原文范围内', sample.hits.every((h) => h.evidence.length && h.evidence.every((e) => e.line >= 1 && e.line <= h.lineCount)), `${sample.hits.map((h) => `${h.name} → L${h.evidence.map((e) => e.line).join(',')}`).join(' | ')} · ik 接口 ${JSON.stringify(sample.meta.tokens)} ${sample.meta.tokenizeError || ''}`);
  const empty = await searcher.search(handle.pool, { q: '   ' });
  ok('空查询友好反馈且不报错', empty.meta.mode === 'empty' && /请输入检索词/.test(empty.meta.note) && empty.hits.length === 0, empty.meta.note);
  const miss = await searcher.search(handle.pool, { q: 'zzqxnorarevphft' });
  log('失配词', miss);
  ok('失配给出可执行提示', miss.meta.mode === 'miss' && /未命中/.test(miss.meta.note) && miss.hits.length === 0, miss.meta.note);

  console.log('\n[4] 项目 / 数据集筛选收窄命中');
  const byProject = await searcher.search(handle.pool, { q: '复现实验', project: '院前呼吸衰竭转运' });
  log('复现实验 + 项目=院前呼吸衰竭转运', byProject);
  ok('项目筛选后新记录命中消失', zh.hits.some((h) => h.id === freshId) && !byProject.hits.some((h) => h.id === freshId), `不限 ${zh.hits.length} 条 → 筛选后 ${byProject.hits.length} 条`);
  ok('筛选 SQL 带全部传参', byProject.meta.params.length === 3 && byProject.meta.sql.includes('project = ?'), JSON.stringify(byProject.meta.params));
  const byDataset = await searcher.search(handle.pool, { q: 'accuracy', dataset: 'SleepEDF-20' });
  log('accuracy + 数据集=SleepEDF-20', byDataset);
  ok('数据集筛选生效', byDataset.hits.length >= 1 && byDataset.hits.every((h) => h.dataset === 'SleepEDF-20'), `命中 ${byDataset.hits.length} 条`);

  console.log('\n[5] 两条实验对比与可比性');
  const good = await cmp.compare(handle.pool, ids['01'].id, ids['02'].id);
  const acc = good.rows.find((r) => r.metric === 'accuracy');
  ok('同数据集/划分/单位 → 可比', good.comparable === true, good.issues.map((i) => i.type).join(',') || '无警告');
  ok('指标变化计算正确', acc && Math.abs(acc.delta - 7.5) < 1e-6 && acc.pct === 10.5, `${acc?.a}${acc?.unitA} → ${acc?.b}${acc?.unitB} Δ${acc?.delta}（${acc?.pct}%）`);
  ok('各自数据集与划分随对比返回', good.records.every((r) => r.dataset && r.split), good.records.map((r) => `${r.name}:${r.dataset}/${r.split}`).join(' | '));
  const md = cmp.toMarkdown(good);
  ok('导出摘要含证据行号与指纹', /第 \d+ 行/.test(md) && /指纹/.test(md));
  const bad = await cmp.compare(handle.pool, ids['02'].id, ids['03'].id);
  ok('数据集不同被标记', bad.issues.some((i) => i.type === 'dataset'), bad.issues.map((i) => i.type).join(','));
  ok('单位不一致 → 不可直接比较', bad.issues.some((i) => i.type === 'unit') && bad.comparable === false, bad.issues.find((i) => i.type === 'unit')?.message);

  console.log('\n[6] 关闭后重开同一目录：持久化');
  const fp = (await store.getRecord(handle.pool, ids['01'].id)).fingerprint;
  await handle.close();
  handle = await db.open({ dataDir: dir, memoryBudget: cfg.memoryBudget, database: cfg.database, poolSize: cfg.poolSize });
  schema = await db.initSchema(handle.pool);
  const after = await searcher.search(handle.pool, { q: '复现实验' });
  log('重启后 复现实验', after);
  ok('重启后新记录仍可检索', after.meta.mode === 'fulltext' && after.hits.some((h) => h.id === freshId), `命中 ${after.hits.length} 条，解析器 ${schema.ftParser || 'default'}`);
  ok('重启后计划仍走索引', /TEXT RETRIEVAL SCAN/.test(planText(after.meta.explain)), planText(after.meta.explain).slice(0, 120));
  ok('重启后指纹一致', (await store.getRecord(handle.pool, ids['01'].id)).fingerprint === fp, fp.slice(0, 12) + '…');
  await handle.close();

  const doc = [
    '# seekdb 检索取证（由 npm test 真实运行生成）',
    '',
    `- 生成时间：${new Date().toISOString()} · 平台 ${process.platform} · Node ${process.version}`,
    `- @oceanbase/seekdb-node 1.4.0 · 服务端 \`${ver.v}\` · 连接通道 ${transport}（connectionOptions() 判别式）· 库 ${cfg.database} · utf8mb4`,
    `- 索引 DDL：\`${schema.indexDdl}\` · 解析器 \`${schema.ftParser || 'default'}\`${schema.ftError ? ` · 降级原因：${schema.ftError}` : ''}`,
    `- 检索方式：自然语言模式 \`MATCH(body) AGAINST (?)\`，EXPLAIN 使用同一条 SQL 与同一组参数`,
    `- memory_budget：\`${cfg.memoryBudget}\``,
    `- 主流程断言：${pass} 通过，${fail} 失败`,
    '',
    ...evidence.map((e) => [
      `## ${e.label}`, '',
      `- 模式：${e.mode} · 命中：${e.hits.map((h) => `#${h.id} ${h.name}（score ${h.score.toFixed(4)}，行 ${h.lines.join('/')}）`).join('；') || '无'}`,
      e.tokens ? `- 该查询的 ik 词元（\`SELECT TOKENIZE(?, 'ik')\`）：${JSON.stringify(e.tokens)}${e.tokenizeError ? ` · 获取失败：${e.tokenizeError}` : ''}` : '',
      e.terms ? `- 证据行定位词（原词 + 词元）：${JSON.stringify(e.terms)}` : '',
      e.note ? `- 界面提示：${e.note}` : '',
      '', '执行的 SQL（占位符即实际语句，不含用户输入原文）：', '```sql', e.sql, '```', `- 传参：\`${JSON.stringify(e.params)}\``, '', 'EXPLAIN 原文：', '```json', JSON.stringify(e.explain ?? { error: e.explainError }, null, 2), '```',
    ].filter(Boolean).join('\n')),
    '',
  ].join('\n');
  fs.mkdirSync(path.join(ROOT, 'docs'), { recursive: true });
  fs.writeFileSync(path.join(ROOT, 'docs', 'seekdb-evidence.md'), doc);
  console.log(`\n取证文件已更新：docs/seekdb-evidence.md（${evidence.length} 组真实 SQL + EXPLAIN）`);
  try {
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 4, retryDelay: 150 });
  } catch (error) {
    if (!['EPERM', 'EBUSY'].includes(error.code)) throw error;
    console.warn(`seekdb 仍占用测试目录，暂时保留 ${dir}`);
  }
  console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail ? 1 : 0);
}

run().catch((e) => { console.error('测试异常：', e.stack); process.exit(1); });
