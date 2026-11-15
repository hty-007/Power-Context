import crypto from 'node:crypto';

// ---------- 指纹：忽略行尾空白/CRLF/连续空行，同一内容重复导入得到同一指纹 ----------
export function normalize(text) {
  return text
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((l) => l.replace(/[ \t]+$/g, ''))
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export function fingerprint(markdown) {
  return crypto.createHash('sha256').update(normalize(markdown), 'utf8').digest('hex');
}

// ---------- 元信息 ----------
const META_KEYS = {
  project: ['项目', '所属项目', 'project'],
  dataset: ['数据集', 'dataset'],
  split: ['划分', '数据划分', 'split'],
  task: ['任务', 'task'],
  model: ['模型', 'model'],
  seed: ['随机种子', 'seed'],
};

const cleanVal = (v) => normalize(String(v)).replace(/[*_`]/g, '').replace(/\s+/g, ' ').trim();

// `- 项目: 慢阻肺…` / `项目：慢阻肺…` / `**项目**: x`
function parseMeta(lines) {
  const out = {};
  for (const raw of lines) {
    const m = raw.match(/^\s*[-*+]?\s*\**\s*([^:：*]{1,24})\**\s*[:：]\s*(.+)$/);
    if (!m) continue;
    const key = cleanVal(m[1]).toLowerCase();
    const val = cleanVal(m[2]);
    if (!val) continue;
    for (const [field, aliases] of Object.entries(META_KEYS)) {
      if (aliases.includes(key) && !out[field]) out[field] = val;
    }
  }
  return out;
}

function parseTitle(lines, fallback) {
  for (const l of lines) {
    const m = l.match(/^\s*#\s+(.+)$/);
    if (m) return cleanVal(m[1]);
  }
  return fallback;
}

// ---------- 指标：Markdown 表格优先，其次项目符号写法 ----------
const NAME_COLS = ['指标', 'metric', 'name', '指标名'];
const VALUE_COLS = ['数值', '值', 'value', 'result', 'score'];
const UNIT_COLS = ['单位', 'unit'];

const splitRow = (row) => row.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim());
const isDivider = (cells) => cells.length > 0 && cells.every((c) => /^:?-{2,}:?$/.test(c));

function parseValueCell(text, unitCol) {
  const m = String(text).match(/^(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*(%|‰|ms|min|h|MB|GB|x|×)?/);
  if (!m) return null;
  return {
    value: Number(m[1]),
    unit: (unitCol || m[2] || String(text).slice(m[0].length).trim()).slice(0, 24),
    raw: String(text).trim(),
  };
}

function parseTableMetrics(lines) {
  for (let i = 0; i + 1 < lines.length; i++) {
    if (!/^\s*\|.*\|\s*$/.test(lines[i])) continue;
    const header = splitRow(lines[i]).map((h) => cleanVal(h).toLowerCase());
    const nameCol = header.findIndex((h) => NAME_COLS.includes(h));
    if (nameCol === -1) continue;
    if (!/^\s*\|.*\|\s*$/.test(lines[i + 1]) || !isDivider(splitRow(lines[i + 1]))) continue;
    const unitCol = header.findIndex((h) => UNIT_COLS.includes(h));
    let valueCol = header.findIndex((h) => VALUE_COLS.includes(h));
    if (valueCol === -1) valueCol = header.findIndex((h) => h !== nameCol && h !== unitCol);
    const out = [];
    for (let j = i + 2; j < lines.length; j++) {
      if (!/^\s*\|.*\|\s*$/.test(lines[j])) break;
      const cells = splitRow(lines[j]);
      const name = cleanVal(cells[nameCol] || '');
      const parsed = parseValueCell(cells[valueCol] || '', unitCol === -1 ? '' : cleanVal(cells[unitCol] || ''));
      if (!name || !parsed) continue;
      out.push({ name, ...parsed, line: j + 1 });
    }
    return out;
  }
  return [];
}

// `- accuracy: 87.4 %` / `**F1** = 0.91`
function parseBulletMetrics(lines) {
  const out = [];
  lines.forEach((raw, idx) => {
    const m = raw.match(/^\s*[-*+]\s*\**([A-Za-z\u4e00-\u9fa5][\w\u4e00-\u9fa5 @\-/]{0,30})\**\s*[:=]\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*(%|‰|ms|min|h|GB|MB|x|×)?/);
    if (!m) return;
    const name = cleanVal(m[1]);
    if (Object.values(META_KEYS).flat().includes(name.toLowerCase())) return;
    out.push({ name, value: Number(m[2]), unit: cleanVal(m[3] || ''), raw: cleanVal(raw), line: idx + 1 });
  });
  return out;
}

export function parseRecord(markdown, fallbackName = '未命名实验') {
  const text = normalize(markdown);
  const lines = text.split('\n');
  const meta = parseMeta(lines);
  const table = parseTableMetrics(lines);
  const metrics = (table.length ? table : parseBulletMetrics(lines)).map((m) => ({
    ...m,
    key: m.name.toLowerCase().replace(/\s+/g, '_'),
  }));
  return {
    name: parseTitle(lines, fallbackName),
    project: meta.project || '未标注项目',
    dataset: meta.dataset || '未标注数据集',
    split: meta.split || '未标注划分',
    task: meta.task || '',
    model: meta.model || '',
    seed: meta.seed || '',
    metrics,
    body: text,
    lineCount: lines.length,
    fingerprint: fingerprint(text),
    metricSource: table.length ? 'table' : metrics.length ? 'bullet' : 'none',
  };
}

// ---------- 检索证据：命中词所在行 + 原文行号 ----------
export function evidenceLines(body, terms, limit = 3) {
  const out = [];
  body.split('\n').forEach((line, idx) => {
    if (!line.trim()) return;
    const low = line.toLowerCase();
    const hits = terms.filter((t) => t && low.includes(t.toLowerCase()));
    if (hits.length) out.push({ line: idx + 1, text: line.trim(), hits: hits.length });
  });
  out.sort((a, b) => b.hits - a.hits || a.line - b.line);
  return out.slice(0, limit);
}
