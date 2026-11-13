import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import os from 'node:os';
import { join } from 'node:path';
import { chromium } from 'playwright';
import { laterNote, querySet, sampleNotes } from '../data/sample.mjs';

const origin = new URL(process.env.NOTES_TEST_URL || 'http://127.0.0.1:4180');
const directory = process.env.NOTES_EVIDENCE_DIR || '.runtime/evidence';
const id = randomUUID().slice(0, 8);
const modes = ['semantic', 'hybrid'];
const keysByTitle = new Map(sampleNotes.map(note => [note.title, note.key]));
const repeats = 3;
const reports = [];
const checks = [];
const foreignRequests = new Set();
const launchOptions = { headless: true };
if (process.env.NOTES_BROWSER) launchOptions.channel = process.env.NOTES_BROWSER;
if (process.env.NOTES_HEADED === '1') launchOptions.headless = false;

await mkdir(directory, { recursive: true });

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function quality(queries) {
  let recall = 0;
  let reciprocal = 0;
  let ndcg = 0;
  for (const { relevant, returned } of queries) {
    const top = [...new Set(returned)].slice(0, 5);
    const scores = top.map(key => Number(relevant.includes(key)));
    recall += scores.reduce((sum, value) => sum + value, 0) / relevant.length;
    reciprocal += scores.includes(1) ? 1 / (scores.indexOf(1) + 1) : 0;
    const discount = index => 1 / Math.log2(index + 2);
    const ideal = relevant.slice(0, 5).reduce((sum, _, i) => sum + discount(i), 0);
    ndcg += scores.reduce((sum, score, i) => sum + score * discount(i), 0) / ideal;
  }
  return { recallAt5: recall / queries.length, mrrAt5: reciprocal / queries.length, ndcgAt5: ndcg / queries.length };
}

async function newPage(browser) {
  const context = await browser.newContext({ viewport: { width: 1360, height: 920 }, locale: 'zh-CN' });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin === origin.origin || ['blob:', 'data:'].includes(url.protocol)) return route.continue();
    foreignRequests.add(url.origin);
    return route.abort();
  });
  const page = await context.newPage();
  page.on('pageerror', error => console.error('Browser page error:', error.message));
  await page.goto(`${origin.origin}/?acceptance=1`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelector('#database-label')?.textContent === 'seekdb 已连接' &&
    !!document.querySelector('#libraries button[aria-current]'));
  return page;
}

async function verifyUI(page, expectedDevice) {
  const library = `验收笔记 ${id}`;
  await page.getByRole('button', { name: '新建知识库' }).click();
  await page.getByRole('textbox', { name: '名称' }).fill(library);
  await page.getByRole('button', { name: '创建', exact: true }).click();
  await page.getByRole('heading', { name: library, exact: true }).waitFor();
  await page.getByRole('button', { name: '导入示例笔记' }).click();
  await page.getByText('已新增 28 段笔记。', { exact: true }).waitFor({ timeout: 180_000 });
  if (expectedDevice === 'wasm') await page.getByText('BGE Small · INT8 · 已自动降级', { exact: true }).waitFor();
  else await page.getByText('WebGPU · 浏览器推理', { exact: true }).waitFor();

  await page.getByRole('button', { name: '二级索引为什么要回表？', exact: true }).click();
  await page.getByRole('heading', { name: '主键以外的第二次查找', exact: true }).waitFor({ timeout: 60_000 });
  await page.screenshot({ path: join(directory, `01-search-${expectedDevice}.png`), animations: 'disabled' });
  checks.push(`UI ${expectedDevice}: 示例导入、回表查询、原文及后端标识可见`);

  await page.locator('#note-files').setInputFiles({ name: `${laterNote.title}.md`, mimeType: 'text/markdown', buffer: Buffer.from(laterNote.text) });
  await page.getByText('已新增 1 段笔记。', { exact: true }).waitFor({ timeout: 60_000 });
  await page.locator('#note-files').setInputFiles({ name: `${laterNote.title}.md`, mimeType: 'text/markdown', buffer: Buffer.from(laterNote.text) });
  await page.getByText('已新增 0 段笔记，跳过 1 段重复内容。', { exact: true }).waitFor();
  await page.getByRole('searchbox', { name: '搜索笔记' }).fill('蓝松校验词');
  await page.getByRole('button', { name: '搜索', exact: true }).click();
  await page.getByRole('heading', { name: laterNote.title, exact: true }).waitFor({ timeout: 60_000 });
  await page.screenshot({ path: join(directory, `02-incremental-${expectedDevice}.png`), animations: 'disabled' });
  checks.push(`UI ${expectedDevice}: 文件导入后索引可查，相同文件重复导入保持计数`);

  await page.getByRole('button', { name: '课程笔记', exact: true }).click();
  await page.getByText('知识库还是空的', { exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: laterNote.title, exact: true }).count(), 0);
  await page.getByRole('button', { name: library, exact: true }).click();
  await page.getByText('29 段笔记', { exact: true }).waitFor();
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: library, exact: true }).waitFor();
  await page.getByText('29 段笔记', { exact: true }).waitFor();
  checks.push(`UI ${expectedDevice}: 集合切换清空上个集合结果，刷新保留选中集合及数据`);
}

async function benchmark(browser, device, precision, batchSize, repeat, evaluateQuality) {
  const page = await newPage(browser);
  try {
    const res = await page.evaluate(async ({ notes, requests, precision, device, batchSize, label, modes }) => {
      const { api, ingest, query } = window.courseNotesAcceptance;
      const library = await api('/libraries', { name: label, precision });
      const options = { preference: device, batchSize };
      const importTimes = await ingest(library, notes, options);
      const results = {};
      for (const mode of modes) {
        const turns = [];
        for (const example of requests) {
          const r = await query(library, example.query, mode, options);
          turns.push({ ...example, titles: r.rows.map(row => row.title), inferenceMs: r.inferenceMs, dbMs: r.dbMs, totalMs: r.totalMs });
        }
        results[mode] = turns;
      }
      return { importTimes, results };
    }, {
      notes: sampleNotes, requests: evaluateQuality ? querySet : querySet.slice(0, 3), precision, device, batchSize,
      label: `B ${id} ${device} ${precision} ${batchSize} ${repeat}`, modes: evaluateQuality ? modes : ['hybrid'],
    });
    assert.equal(res.importTimes.inserted, sampleNotes.length);
    assert.equal(res.importTimes.device, device);
    const result = { device, precision, batchSize, repeat, import: res.importTimes, searches: {} };
    for (const [mode, turns] of Object.entries(res.results)) {
      assert.ok(turns.every(turn => turn.titles.length > 0));
      const measured = turns.map(({ titles, ...turn }) => ({ ...turn, returned: titles.map(title => keysByTitle.get(title)) }));
      result.searches[mode] = {
        quality: quality(measured), firstAfterImport: measured[0],
        median: Object.fromEntries(['inferenceMs', 'dbMs', 'totalMs'].map(key => [key, median(measured.slice(1).map(turn => turn[key]))])),
        queries: measured,
      };
    }
    reports.push(result);
    console.log(`${device} ${precision} batch=${batchSize} repeat=${repeat} imported=${res.importTimes.inserted} inference=${res.importTimes.inferenceMs.toFixed(1)}ms hybrid Recall@5=${result.searches.hybrid.quality.recallAt5.toFixed(3)}`);
  } finally {
    await page.context().close();
  }
}

let status;
let browserVersion;
let webgpu = null;
const profile = process.env.NOTES_TEST_PROFILE || 'full';
const browser = await chromium.launch(launchOptions);
try {
  browserVersion = browser.version();
  const initial = await newPage(browser);
  status = await initial.evaluate(() => window.courseNotesAcceptance.api('/status'));
  webgpu = await initial.evaluate(async () => {
    const adapter = await navigator.gpu?.requestAdapter({ powerPreference: 'high-performance' });
    return adapter ? { vendor: adapter.info.vendor, architecture: adapter.info.architecture, fallback: adapter.info.isFallbackAdapter } : null;
  });
  await initial.context().close();
  console.log('Environment', JSON.stringify({ browserVersion, seekdb: status.version, webgpu, profile }));
  if (profile === 'gpu') assert.ok(webgpu, 'A real WebGPU adapter is required for the GPU profile.');
  if (webgpu && profile !== 'wasm') {
    const visible = await newPage(browser);
    await verifyUI(visible, 'webgpu');
    await visible.context().close();
    for (let i = 1; i <= repeats; i += 1) await benchmark(browser, 'webgpu', 'q8', 8, i, true);
  }
  if (profile !== 'gpu') {
    const fallback = await chromium.launch({ ...launchOptions, args: ['--disable-webgpu'] });
    try {
      const visible = await newPage(fallback);
      assert.equal(await visible.evaluate(async () => Boolean(await navigator.gpu?.requestAdapter())), false);
      await verifyUI(visible, 'wasm');
      await visible.context().close();
      for (const precision of ['fp32', 'q8']) {
        for (let i = 1; i <= repeats; i += 1) await benchmark(fallback, 'wasm', precision, 8, i, true);
      }
      for (let i = 1; i <= repeats; i += 1) await benchmark(fallback, 'wasm', 'q8', 1, i, false);
    } finally {
      await fallback.close();
    }
  }
  assert.equal(foreignRequests.size, 0, 'No external browser requests during imports/search');
  checks.push('浏览器在禁止所有外部请求的条件下完成导入与检索');
} finally {
  await browser.close();
  await writeFile(join(directory, 'measurements.json'), `${JSON.stringify({
    testedAt: new Date().toISOString(), profile, browserVersion, webgpu,
    system: { platform: os.platform(), release: os.release(), cpu: os.cpus()[0]?.model, logicalCPUs: os.cpus().length },
    seekdb: status?.version, model: status?.model, assets: status?.assets, queries: querySet.length, documents: sampleNotes.length,
    foreignRequests: [...foreignRequests], checks, reports,
  }, null, 2)}\n`);
}
