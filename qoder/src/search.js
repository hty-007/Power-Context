import { evidenceLines } from './parse.js';

export const termsOf = (q) => String(q || '').trim().split(/[\s,，、;；]+/).filter(Boolean).slice(0, 8);

const clampLimit = (n) => Math.max(1, Math.min(Number(n) || 20, 100));

const SELECT = `SELECT id, name, project, dataset, split, source, body, line_count,
       MATCH(body) AGAINST (?) AS score
  FROM exp_record
 WHERE MATCH(body) AGAINST (?)`;

const toHit = (r, terms) => ({
  id: Number(r.id),
  name: r.name,
  project: r.project,
  dataset: r.dataset,
  split: r.split,
  source: r.source,
  lineCount: Number(r.line_count),
  score: Number(r.score ?? 0),
  terms,
  evidence: evidenceLines(r.body, terms),
});

// TOKENIZE 的返回形态不固定：可能是数组、JSON 字符串，也可能是每行一个词元
const firstCell = (row) => (row && typeof row === 'object' && !Array.isArray(row) ? (row.t ?? Object.values(row)[0]) : row);

function parseTokens(value) {
  let v = value;
  if (Buffer.isBuffer(v)) v = v.toString('utf8');
  if (typeof v === 'string') {
    const s = v.trim();
    if (!s) return [];
    try { v = JSON.parse(s); } catch { return [s]; }
  }
  if (!Array.isArray(v)) return [];
  return v
    .map((x) => {
      if (x === null || x === undefined) return '';
      if (typeof x === 'string' || typeof x === 'number') return String(x);
      return String(x.word ?? x.token ?? x.content ?? x.value ?? '');
    })
    .map((s) => s.trim())
    .filter(Boolean);
}

/** 用与索引同一个 ik 分词器取查询的实际词元，避免自然语言模式的部分命中拿不到证据行。 */
async function queryTokens(client, query) {
  try {
    const [rows] = await client.execute(`SELECT TOKENIZE(?, 'ik') AS t`, [query]);
    const tokens = [];
    for (const row of rows) for (const t of parseTokens(firstCell(row))) if (!tokens.includes(t)) tokens.push(t);
    return { tokens, error: '' };
  } catch (e) {
    return { tokens: [], error: e.message };
  }
}

/** 原词 + 实际词元一起用于定位证据行；长词优先，因此包含完整原词的行排在前面。 */
function locateTerms(query, terms, tokens) {
  const low = query.toLowerCase();
  const usable = tokens.filter((t) => t && low.includes(t.toLowerCase()));
  const all = [...terms, ...usable];
  return [...new Map(all.map((t) => [t.toLowerCase(), t]).values()).values()]
    .sort((a, b) => b.length - a.length)
    .slice(0, 12);
}

/**
 * 检索主路径：seekdb FULLTEXT 索引 + 自然语言模式（实机确认 EXPLAIN 为 TEXT RETRIEVAL SCAN）。
 * 参数全部走占位符传参，EXPLAIN 用的就是这条 SQL 与同一组参数。
 */
export async function search(client, { q, project = '', dataset = '', limit = 20, explain = true }) {
  const query = String(q || '').trim().slice(0, 200);
  const terms = termsOf(query);

  if (!query) {
    return {
      query: '',
      hits: [],
      meta: { mode: 'empty', sql: '', params: [], explain: null, note: '请输入检索词：指标名（accuracy）、结论用词（学习率）、数据集或专有词都可以。' },
    };
  }

  const conds = [];
  const params = [query, query];
  if (project) { conds.push('project = ?'); params.push(project); }
  if (dataset) { conds.push('dataset = ?'); params.push(dataset); }
  const where = conds.length ? ` AND ${conds.join(' AND ')}` : '';
  const sql = `${SELECT}${where}
 ORDER BY score DESC, id ASC
 LIMIT ${clampLimit(limit)}`;

  const meta = { mode: 'fulltext', sql, params, explain: null, note: '' };
  const [rows] = await client.execute(sql, params);

  if (explain) {
    // EXPLAIN 解释的就是上面这条语句与这组参数；预处理形式不被接受时改由驱动内联同一组参数
    try {
      const [ex] = await client.execute(`EXPLAIN ${sql}`, params);
      meta.explain = ex;
    } catch (e) {
      try {
        const [ex] = await client.query(`EXPLAIN ${sql}`, params);
        meta.explain = ex;
        meta.explainMode = 'client-interpolated';
      } catch (e2) {
        meta.explain = null;
        meta.explainError = `${e.message} / ${e2.message}`;
      }
    }
  }

  if (!rows.length) {
    meta.mode = 'miss';
    meta.note = '索引未命中：请换成与记录原文一致的写法（中英文别名分开各试一次），或去掉项目/数据集筛选后重试。';
    return { query, hits: [], meta };
  }

  // 有命中才付分词代价：自然语言模式可能按 ik 的部分词元命中，用实际词元定位证据行才不编造片段
  const { tokens, error } = await queryTokens(client, query);
  const found = locateTerms(query, terms, tokens);
  meta.tokens = tokens;
  meta.tokenizeError = error;
  meta.terms = found;
  const hits = rows.map((r) => toHit(r, found));
  const added = found.filter((t) => !terms.some((o) => o.toLowerCase() === t.toLowerCase()));
  if (hits.some((h) => !h.evidence.length)) {
    meta.note = '有记录命中但定位不到含原词或实际词元的行，请展开记录正文核对。';
  } else if (added.length) {
    meta.note = `证据行按 ik 实际词元定位：${added.join(' / ')}（自然语言模式可能只按部分词元命中）。`;
  }
  return { query, hits, meta };
}
