import { prepareNotes } from '../shared/notes.mjs';

const worker = new Worker(new URL('./embed.worker.js', import.meta.url), { type: 'module' });
const jobs = new Map();
let nextId = 0;

worker.addEventListener('message', event => {
  const { id, progress, error, result } = event.data;
  const job = jobs.get(id);
  if (!job) return;
  if (progress) {
    job.onProgress(progress);
    return;
  }
  jobs.delete(id);
  if (error) job.reject(new Error(error));
  else job.resolve(result);
});

worker.addEventListener('error', () => {
  for (const job of jobs.values()) job.reject(new Error('浏览器推理进程未能运行。请刷新页面后重试。'));
  jobs.clear();
});

export async function api(path, data) {
  const response = await fetch(`/api${path}`, data === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `服务返回 HTTP ${response.status}`);
  return result;
}

function embedding(texts, { precision, preference = 'auto', batchSize = 8 }, onProgress) {
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    jobs.set(id, { resolve, reject, onProgress });
    worker.postMessage({ id, texts, precision, preference, batchSize });
  });
}

export async function ingest(library, notes, options, onProgress = () => {}) {
  const start = performance.now();
  onProgress({ phase: 'prepare' });
  const chunks = await prepareNotes(notes);
  const path = `/libraries/${library.id}`;
  const known = new Set(await api(`${path}/known`, { ids: chunks.map(chunk => chunk.id) }));
  const fresh = chunks.filter(chunk => !known.has(chunk.id));
  if (!fresh.length) {
    const state = await api(path);
    return { ...state, inserted: 0, duplicates: known.size, totalMs: performance.now() - start };
  }

  const encoded = await embedding(fresh.map(chunk => `${chunk.title}\n${chunk.content}`), {
    ...options, precision: library.precision,
  }, onProgress);
  const { vectors, ...timings } = encoded;
  onProgress({ phase: 'write', device: timings.device, precision: library.precision });
  const indexed = await api(`${path}/index`, {
    precision: library.precision, chunks: fresh.map((chunk, i) => ({ ...chunk, vector: vectors[i] })),
  });
  return { ...timings, ...indexed, duplicates: known.size + indexed.duplicates, totalMs: performance.now() - start };
}

export async function query(library, text, mode, options, onProgress = () => {}) {
  const start = performance.now();
  const encoded = await embedding([text], { ...options, precision: library.precision }, onProgress);
  const { vectors, ...timings } = encoded;
  onProgress({ phase: 'search', device: timings.device, precision: library.precision });
  const matches = await api(`/libraries/${library.id}/search`, {
    precision: library.precision, text, vector: vectors[0], mode,
  });
  return { ...timings, ...matches, totalMs: performance.now() - start };
}
