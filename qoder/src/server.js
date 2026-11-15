import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { cfg } from './config.js';
import * as db from './db.js';
import * as store from './store.js';
import * as searcher from './search.js';
import * as cmp from './compare.js';

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml' };
const PUBLIC = cfg.publicDir;
const SAMPLES = cfg.samplesDir;

let handle; // { native, pool, transport, info }
const client = () => handle.pool;

/** 播种演示样例：靠内容指纹天然幂等，可重复调用而不产生副本。 */
export async function seedSamples(target = client()) {
  const files = fs.existsSync(SAMPLES) ? fs.readdirSync(SAMPLES).filter((f) => f.endsWith('.md')).sort() : [];
  const out = [];
  for (const f of files) {
    const r = await store.importRecord(target, fs.readFileSync(path.join(SAMPLES, f), 'utf8'), `samples/${f}`);
    out.push({ file: f, id: r.id, duplicate: r.duplicate });
  }
  return { seeded: out.filter((x) => !x.duplicate).length, existing: out.filter((x) => x.duplicate).length, files: out };
}

const send = (res, status, body, headers = {}) => {
  const data = typeof body === 'string' ? body : JSON.stringify(body);
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...headers });
  res.end(data);
};

function readBody(req, limit = 4 << 20) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > limit) return reject(new Error('请求体过大'));
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

const routes = {
  'GET /api/health': async () => ({ ok: true, ...handle.info, stats: await store.stats(client()) }),
  'GET /api/records': async () => ({ records: await store.listRecords(client()) }),
  'GET /api/record': async (q) => ({ record: await store.getRecord(client(), Number(q.id)) }),
  'GET /api/filters': async () => await store.filters(client()),
  'GET /api/search': async (q) => await searcher.search(client(), { q: q.q || '', project: q.project || '', dataset: q.dataset || '', limit: Number(q.limit) || 20 }),
  'GET /api/compare': async (q) => {
    if (!q.a || !q.b) throw Object.assign(new Error('需要 a 与 b 两个记录 id'), { status: 400 });
    const c = await cmp.compare(client(), q.a, q.b);
    return { ...c, markdown: cmp.toMarkdown(c) };
  },
  'POST /api/seed': async () => await seedSamples(),
  'POST /api/import': async (q, req) => {
    const body = JSON.parse((await readBody(req)) || '{}');
    let markdown = String(body.markdown || '');
    let source = String(body.source || '手动粘贴').slice(0, 200);
    if (!markdown && body.sample) {
      const safe = path.basename(String(body.sample));
      if (!safe.endsWith('.md')) throw Object.assign(new Error('样例文件名需以 .md 结尾'), { status: 400 });
      const p = path.join(SAMPLES, safe);
      if (!fs.existsSync(p)) throw Object.assign(new Error(`样例不存在：${safe}`), { status: 404 });
      markdown = fs.readFileSync(p, 'utf8');
      source = `samples/${safe}`;
    }
    if (!markdown.trim()) throw Object.assign(new Error('markdown 为空'), { status: 400 });
    return await store.importRecord(client(), markdown, source);
  },
};

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || '127.0.0.1'}`);
  try {
    if (url.pathname.startsWith('/api/')) {
      const handler = routes[`${req.method} ${url.pathname}`];
      if (!handler) return send(res, 404, { error: `未知接口 ${req.method} ${url.pathname}` });
      if (!handle) return send(res, 503, { error: 'seekdb 尚未就绪' });
      return send(res, 200, await handler(Object.fromEntries(url.searchParams), req));
    }
    const rel = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/+/, '');
    const file = path.join(PUBLIC, path.normalize(rel));
    if (path.dirname(file) !== PUBLIC && !file.startsWith(PUBLIC + path.sep)) return send(res, 403, '禁止访问', { 'Content-Type': 'text/plain; charset=utf-8' });
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return send(res, 404, '未找到', { 'Content-Type': 'text/plain; charset=utf-8' });
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  } catch (e) {
    send(res, e.status || 500, { error: e.message });
  }
});

export async function main() {
  handle = await db.open({ dataDir: cfg.dataDir, memoryBudget: cfg.memoryBudget, database: cfg.database, poolSize: cfg.poolSize });
  const schema = await db.initSchema(handle.pool);
  const seeded = await seedSamples();
  const [[ver]] = await handle.pool.query('SELECT VERSION() AS v');
  handle.info = {
    dataDir: cfg.dataDir,
    transport: handle.transport,
    user: handle.endpoint.user,
    database: cfg.database,
    serverVersion: ver.v,
    charset: 'utf8mb4',
    ftParser: schema.ftParser || 'default',
    ftIndexDdl: schema.indexDdl,
    ftInitError: schema.ftError || '',
    demoNew: seeded.seeded,
    demoExisting: seeded.existing,
  };
  server.listen(cfg.port, cfg.host, () => {
    console.log(`实验溯源台已启动：http://${cfg.host}:${cfg.port}`);
    console.log(`  数据目录 ${cfg.dataDir} · memory_budget ${cfg.memoryBudget}`);
    console.log(`  seekdb ${handle.info.serverVersion} · 连接 ${handle.transport} · 库 ${cfg.database} · utf8mb4`);
    console.log(`  FULLTEXT 索引 ${schema.indexDdl}${schema.ftError ? `\n  （${schema.ftError}，已退回默认解析器）` : ''}`);
    if (seeded.seeded) console.log(`  首次运行：新导入 ${seeded.seeded} 份演示样例（虚构数据）`);
    else if (seeded.existing) console.log(`  演示样例已在库中：${seeded.existing} 份（指纹相同，未重复导入）`);
  });
  const bye = async () => { try { await handle.close(); } catch {} process.exit(0); };
  process.on('SIGINT', bye);
  process.on('SIGTERM', bye);
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));
if (isMain) main().catch((e) => { console.error('启动失败：', e.message); process.exit(1); });
