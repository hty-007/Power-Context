// 一次性命令行工具（取证与脚本化用）：node src/cli.js <seed|search|explain|evidence> [参数]
import fs from 'node:fs';
import path from 'node:path';
import { cfg } from './config.js';
import * as db from './db.js';
import * as store from './store.js';
import * as searcher from './search.js';

async function withDb(fn) {
  const h = await db.open({ dataDir: cfg.dataDir, memoryBudget: cfg.memoryBudget, database: cfg.database, poolSize: cfg.poolSize });
  const schema = await db.initSchema(h.pool);
  try { return await fn(h.pool, schema, h); } finally { await h.close(); }
}

const listSamples = () => fs.readdirSync(cfg.samplesDir).filter((x) => x.endsWith('.md')).sort();

const cmds = {
  seed: () => withDb(async (pool) => {
    for (const f of listSamples()) {
      const r = await store.importRecord(pool, fs.readFileSync(path.join(cfg.samplesDir, f), 'utf8'), `samples/${f}`);
      console.log(`${r.duplicate ? '重复跳过' : '导入'} #${r.id} ${r.parsed.name}（指标 ${r.parsed.metrics.length}）`);
    }
  }),
  search: (q) => withDb(async (pool) => {
    const r = await searcher.search(pool, { q: q || '学习率' });
    console.log(`模式 ${r.meta.mode} · 命中 ${r.hits.length}`);
    for (const h of r.hits) console.log(` #${h.id} ${h.name} [${h.dataset}/${h.split}] score ${h.score.toFixed(4)} → ${h.evidence.map((e) => `L${e.line}`).join(' ')}`);
    if (r.meta.note) console.log('提示：' + r.meta.note);
  }),
  explain: (q) => withDb(async (pool) => {
    const r = await searcher.search(pool, { q: q || '复现实验' });
    console.log('SQL:\n' + r.meta.sql);
    console.log('参数:', JSON.stringify(r.meta.params));
    console.log('ik 词元:', JSON.stringify(r.meta.tokens ?? []), r.meta.tokenizeError ? `（获取失败：${r.meta.tokenizeError}）` : '');
    console.log('证据行定位词:', JSON.stringify(r.meta.terms ?? []));
    console.log('EXPLAIN:', JSON.stringify(r.meta.explain, null, 2));
  }),
  evidence: () => withDb(async (pool, schema, h) => {
    const words = ['复现实验', '学习率', 'accuracy', '队列停滞', '绝对不存在的词zzqqxx'];
    const blocks = [];
    for (const w of words) {
      const r = await searcher.search(pool, { q: w });
      blocks.push([
        `## 查询词 \`${w}\``,
        '',
        `- 模式：${r.meta.mode} · 命中 ${r.hits.length} 条`,
        r.meta.tokens ? `- 该查询的 ik 词元（\`SELECT TOKENIZE(?, 'ik')\`）：${JSON.stringify(r.meta.tokens)}${r.meta.tokenizeError ? ` · 获取失败：${r.meta.tokenizeError}` : ''}` : '',
        r.meta.terms ? `- 证据行定位词：${JSON.stringify(r.meta.terms)}` : '',
        `- 命中记录与原文行号：${r.hits.map((x) => `#${x.id} ${x.name}（score ${x.score.toFixed(4)}，行 ${x.evidence.map((e) => e.line).join('/')}）`).join('；') || '无'}`,
        r.meta.note ? `- 界面提示文案：${r.meta.note}` : '',
        '',
        '```sql', r.meta.sql, '```',
        `- 传参：\`${JSON.stringify(r.meta.params)}\``,
        '',
        'EXPLAIN 原文（同一条 SQL、同一组参数）：',
        '```json', JSON.stringify(r.meta.explain, null, 2), '```',
      ].filter(Boolean).join('\n'));
    }
    const [[ver]] = await pool.query('SELECT VERSION() AS v');
    const st = await store.stats(pool);
    const doc = [
      '# seekdb 检索取证（真实运行生成）',
      '',
      `- 生成时间：${new Date().toISOString()} · 平台 ${process.platform} · Node ${process.version}`,
      `- @oceanbase/seekdb-node 1.4.0 · 服务端 \`${ver.v}\` · 连接通道 ${h.transport}（connectionOptions() 判别式）· 库 ${cfg.database} · utf8mb4`,
      `- 索引：\`${schema.indexDdl}\` · 解析器 \`${schema.ftParser || 'default'}\`${schema.ftError ? ` · 降级原因：${schema.ftError}` : ''} · 检索方式：自然语言模式 \`MATCH(body) AGAINST (?)\``,
      `- 库内：${st.records} 份记录 / ${st.metrics} 项指标 · 去重：${st.fingerprintAlgorithm} + exp_record 唯一索引`,
      '',
      ...blocks,
      '',
    ].join('\n');
    fs.mkdirSync(path.join(cfg.root, 'docs'), { recursive: true });
    const p = path.join(cfg.root, 'docs', 'seekdb-evidence.md');
    fs.writeFileSync(p, doc);
    console.log('已写入', p);
  }),
};

const [cmd, ...rest] = process.argv.slice(2);
if (!cmd || !cmds[cmd]) console.log(`用法: node src/cli.js <${Object.keys(cmds).join('|')}> [参数]`);
else await cmds[cmd](rest.join(' '));
