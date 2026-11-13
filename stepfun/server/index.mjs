import { readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';
import { createStorage } from './storage.mjs';
import { ASSETS, ASSET_SHA256, MODEL } from '../shared/model.mjs';
import { MAX_CHUNKS, MAX_QUERY_LENGTH } from '../shared/notes.mjs';

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const runtime = join(ROOT, '.runtime');
process.env.SEEKDB_BINDINGS_CACHE_DIR ??= join(runtime, 'bindings');
const data = resolve(process.env.NOTES_DATA_DIR || join(runtime, 'library'));
const port = Number(process.env.NOTES_PORT || 4180);
const models = join(runtime, 'models');
const manifest = JSON.parse(await readFile(join(models, MODEL.id, 'manifest.json'), 'utf8'));
if (manifest.revision !== MODEL.revision || !ASSETS.every(file => manifest.files?.[file]?.sha256 === ASSET_SHA256[file])) {
  throw new Error('Missing model assets. Run pnpm models before starting.');
}
const serverOptions = process.env.NOTES_DB_HOST ? {
  host: process.env.NOTES_DB_HOST,
  port: Number(process.env.NOTES_DB_PORT || 2881),
  database: process.env.NOTES_DB_DATABASE || 'test',
  user: process.env.NOTES_DB_USER || 'root',
  password: process.env.NOTES_DB_PASSWORD || '',
} : undefined;
const storage = createStorage(data, serverOptions);
const version = await storage.initialize();
const app = express();
app.disable('x-powered-by');
app.use((req, res, next) => {
  res.set({
    'Cross-Origin-Opener-Policy': 'same-origin',
    'Cross-Origin-Embedder-Policy': 'require-corp',
    'Cross-Origin-Resource-Policy': 'same-origin',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data:; worker-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'",
  });
  if (!/^(?:127\.0\.0\.1|localhost)(?::\d+)?$/i.test(req.headers.host ?? '')) {
    return res.status(403).json({ error: '请用本机地址打开。' });
  }
  if (req.method === 'POST' && req.headers.origin && req.headers.origin !== `http://${req.headers.host}`) {
    return res.status(403).json({ error: '请从应用页面提交操作。' });
  }
  next();
});
app.use('/api', express.json({ limit: '2mb' }));
app.use(`/models/${MODEL.revision}`, express.static(models, { maxAge: '7d', immutable: true }));

function valid(condition, message) {
  if (!condition) throw Object.assign(new Error(message), { status: 400 });
}

function checkedId(id) {
  valid(/^notes_[0-9a-f]{24}$/.test(id), '知识库编号无效。');
  return id;
}

function checkedIds(ids) {
  valid(Array.isArray(ids) && ids.length > 0 && ids.length <= MAX_CHUNKS && ids.every(id => /^[0-9a-f]{64}-[1-9]\d{0,2}$/.test(id)), '笔记编号无效。');
  return [...new Set(ids)];
}

function checkedPrecision(precision) {
  valid(Object.hasOwn(MODEL.weights, precision), '模型精度无效。');
  return precision;
}

function vectorOK(vector) {
  return Array.isArray(vector) && vector.length === MODEL.dimension && vector.every(value => Number.isFinite(value) && Math.abs(value) <= 1.0001)
    && vector.some(value => value !== 0);
}

function api(method, path, operation) {
  app[method](path, async (req, res) => {
    res.set('Cache-Control', 'no-store');
    const result = await storage.sequential(() => operation(req));
    res.json(result);
  });
}

api('get', '/api/status', () => ({ database: 'seekdb', version, model: MODEL, assets: manifest.files }));
api('get', '/api/libraries', () => storage.listLibraries());
api('post', '/api/libraries', req => {
  const name = typeof req.body?.name === 'string' ? req.body.name.normalize('NFC').trim() : '';
  valid(name.length >= 1 && name.length <= 36 && !/[\p{Cc}]/u.test(name), '知识库名称须为 1–36 个字符。');
  return storage.createLibrary(name, checkedPrecision(req.body.precision));
});
api('get', '/api/libraries/:id', req => storage.state(checkedId(req.params.id)));
api('post', '/api/libraries/:id/known', req => storage.existing(checkedId(req.params.id), checkedIds(req.body?.ids)));
api('post', '/api/libraries/:id/index', req => {
  const chunks = req.body?.chunks;
  valid(Array.isArray(chunks) && chunks.length > 0 && chunks.length <= MAX_CHUNKS, '每次导入须包含 1–100 段内容。');
  valid(chunks.every(chunk => chunk && typeof chunk === 'object'), '笔记记录格式无效。');
  valid(checkedIds(chunks.map(chunk => chunk.id)).length === chunks.length, '本批笔记编号重复。');
  valid(chunks.every(chunk => (
    typeof chunk.title === 'string' && chunk.title.trim().length > 0 && chunk.title.length <= 120
    && typeof chunk.content === 'string' && chunk.content.trim().length > 0 && chunk.content.length <= 560
    && typeof chunk.noteId === 'string' && /^[0-9a-f]{64}$/.test(chunk.noteId)
    && Number.isInteger(chunk.part) && Number.isInteger(chunk.totalParts)
    && chunk.part > 0 && chunk.part <= chunk.totalParts && chunk.totalParts <= MAX_CHUNKS
    && chunk.id === `${chunk.noteId}-${chunk.part}` && vectorOK(chunk.vector)
  )), '笔记内容、段号或向量格式无效。');
  return storage.index(checkedId(req.params.id), checkedPrecision(req.body.precision), chunks);
});
api('post', '/api/libraries/:id/search', req => {
  const { precision, vector, mode } = req.body ?? {};
  const text = typeof req.body?.text === 'string' ? req.body.text.trim() : '';
  valid(text.length >= 1 && text.length <= MAX_QUERY_LENGTH, '请输入 1–140 个字符。');
  valid(mode === 'hybrid' || mode === 'semantic', '检索方式无效。');
  valid(vectorOK(vector), '查询向量格式无效。');
  return storage.search(checkedId(req.params.id), checkedPrecision(precision), text, vector, mode);
});

app.use(express.static(join(ROOT, 'dist')));
app.use((req, res) => res.status(404).json({ error: '没有这个页面或接口。' }));
app.use((error, req, res, next) => {
  const status = error.status >= 400 && error.status <= 599 ? error.status : 500;
  if (status >= 500) console.error(`${req.method} ${req.path}: ${error.stack}`);
  res.status(status).json({ error: status >= 500 ? 'seekdb 操作暂未完成，请重试。' : error.message });
});

app.listen(port, '127.0.0.1', () => console.log(`课页 http://127.0.0.1:${port} · seekdb ${version}`));
