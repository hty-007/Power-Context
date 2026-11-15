import { getRecord } from './store.js';

const LOWER_BETTER = /(loss|err|mse|mae|rmse|latency|time|耗时|误差|延迟|cost|开销)/i;
const normUnit = (u) => String(u || '').trim().toLowerCase().replace(/\s/g, '').replace(/百分比/, '%');

function pairRow(a, b) {
  const va = Number(a.value);
  const vb = Number(b.value);
  const delta = vb - va;
  const name = String(a.name);
  const lowerBetter = LOWER_BETTER.test(name);
  return {
    metric: name,
    a: va,
    b: vb,
    unitA: String(a.unit || ''),
    unitB: String(b.unit || ''),
    delta: Number(delta.toFixed(6)),
    pct: va === 0 ? null : Number(((delta / Math.abs(va)) * 100).toFixed(2)),
    direction: delta === 0 ? '持平' : (lowerBetter ? (delta < 0 ? '改善' : '变差') : delta > 0 ? '改善' : '变差'),
    heuristic: lowerBetter ? '越低越好' : '越高越好',
    lineA: Number(a.line_no) || null,
    lineB: Number(b.line_no) || null,
    only: '',
    comparable: true,
  };
}

const sideRow = (m, side) => ({
  metric: String(m.name),
  a: side === 'a' ? Number(m.value) : null,
  b: side === 'b' ? Number(m.value) : null,
  unitA: side === 'a' ? String(m.unit || '') : null,
  unitB: side === 'b' ? String(m.unit || '') : null,
  delta: null,
  pct: null,
  direction: '',
  lineA: side === 'a' ? Number(m.line_no) || null : null,
  lineB: side === 'b' ? Number(m.line_no) || null : null,
  only: side,
});

export function align(aMetrics, bMetrics) {
  const bByKey = new Map(bMetrics.map((m) => [String(m.mkey), m]));
  const seen = new Set();
  const rows = [];
  for (const a of aMetrics) {
    const b = bByKey.get(String(a.mkey));
    if (!b) continue;
    seen.add(String(a.mkey));
    rows.push(pairRow(a, b));
  }
  for (const b of bMetrics) if (!seen.has(String(b.mkey))) rows.push(sideRow(b, 'b'));
  for (const a of aMetrics) if (!seen.has(String(a.mkey))) rows.push(sideRow(a, 'a'));
  return rows;
}

/** 比较两条记录；单位/数据集/划分不一致时标记不可直接比较。 */
export async function compare(conn, idA, idB) {
  const [A, B] = [await getRecord(conn, Number(idA)), await getRecord(conn, Number(idB))];
  if (!A || !B) throw Object.assign(new Error('记录不存在，请重新选择'), { status: 404 });
  const rows = align(A.metrics, B.metrics);
  const issues = [];

  const unitClash = rows.filter((r) => r.only === '' && normUnit(r.unitA) !== normUnit(r.unitB));
  if (unitClash.length) {
    for (const r of unitClash) { r.comparable = false; r.deltaNote = '单位不一致，仅列出原值'; }
    issues.push({
      type: 'unit',
      metrics: unitClash.map((r) => r.metric),
      message: `指标单位不一致，不可直接相减：${unitClash.map((r) => `${r.metric}（${r.unitA || '无单位'} vs ${r.unitB || '无单位'}）`).join('；')}`,
    });
  }
  if (String(A.dataset) !== String(B.dataset)) issues.push({ type: 'dataset', message: `数据集不同：${A.dataset} ≠ ${B.dataset}，差异可能来自数据本身而非改动` });
  if (String(A.split) !== String(B.split)) issues.push({ type: 'split', message: `划分不同：${A.split} ≠ ${B.split}，不可直接比较` });
  if (!rows.some((r) => r.only === '')) issues.push({ type: 'overlap', message: '两条记录没有同名指标，无法计算变化' });

  const shared = rows.filter((r) => r.only === '');
  return {
    comparable: issues.length === 0 && shared.length > 0,
    issues,
    rows,
    records: [A, B].map((r) => ({
      id: Number(r.id),
      name: r.name,
      project: r.project,
      dataset: r.dataset,
      split: r.split,
      model: r.model,
      seed: r.seed,
      source: r.source,
      importedAt: r.imported_at,
      fingerprint: r.fingerprint,
    })),
  };
}

const fmt = (v) => (v === null || v === undefined ? '—' : String(v));

/** 带证据行号引用的 Markdown 摘要，供一键复制 */
export function toMarkdown(c) {
  const [A, B] = c.records;
  const L = [];
  L.push(`# 实验对比：${A.name} → ${B.name}`, '');
  L.push('| 实验 | 项目 | 数据集 | 划分 | 来源 |', '| --- | --- | --- | --- | --- |');
  for (const r of [A, B]) L.push(`| ${r.name} | ${r.project} | ${r.dataset} | ${r.split} | ${r.source || `record#${r.id}`} |`);
  L.push('', '| 指标 | 基线 | 对比 | 变化 | 相对变化 | 说明 |', '| --- | --- | --- | --- | --- | --- |');
  for (const r of c.rows) {
    if (r.only) L.push(`| ${r.metric} | ${fmt(r.a)}${r.unitA || ''} | ${fmt(r.b)}${r.unitB || ''} | 仅单侧存在 | — | ${r.only === 'a' ? '对比记录缺该指标' : '基线缺该指标'} |`);
    else if (!r.comparable) L.push(`| ${r.metric} | ${r.a}${r.unitA} | ${r.b}${r.unitB} | — | — | ${r.deltaNote} |`);
    else L.push(`| ${r.metric} | ${r.a}${r.unitA} | ${r.b}${r.unitB} | ${r.delta > 0 ? '+' : ''}${r.delta} | ${r.pct === null ? '—' : (r.pct > 0 ? '+' : '') + r.pct + '%'} | ${r.direction}（${r.heuristic}） |`);
  }
  if (c.issues.length) {
    L.push('', '## 可比性提示');
    for (const i of c.issues) L.push(`- ${i.message}`);
  } else {
    L.push('', '## 可比性', '- 数据集、划分与指标单位一致，变化列可直接解读。');
  }
  L.push('', '## 证据');
  for (const r of c.rows) {
    const refs = [];
    if (r.lineA) refs.push(`「${A.name}」第 ${r.lineA} 行`);
    if (r.lineB) refs.push(`「${B.name}」第 ${r.lineB} 行`);
    if (refs.length) L.push(`- ${r.metric}：${refs.join(' / ')}`);
  }
  L.push('', `> 记录指纹：A ${A.fingerprint.slice(0, 12)}… / B ${B.fingerprint.slice(0, 12)}…，由实验溯源台基于 seekdb 中的原文生成。`);
  return L.join('\n');
}
