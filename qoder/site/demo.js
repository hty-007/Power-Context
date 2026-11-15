'use strict';
// 站点静态演示逻辑：浏览器内子串匹配，仅用于展示形态；真实索引检索在本地版（qoder/）执行。
const R = window.DEMO_RECORDS || [];
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

function find(q, project) {
  const terms = q.trim().toLowerCase().split(/[\s,，、;；]+/).filter(Boolean);
  return R.filter((r) => !project || r.project === project)
    .map((r) => {
      const ev = r.lines.map((l, i) => ({ n: i + 1, l })).filter((x) => terms.some((t) => x.l.toLowerCase().includes(t)));
      return { ...r, ev };
    })
    .filter((r) => r.ev.length)
    .map((r) => ({ ...r, score: r.ev.length }))
    .sort((a, b) => b.score - a.score);
}

function mark(text, terms) {
  return terms.reduce((s, t) => s.replace(new RegExp(`(${t.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')})`, 'gi'), '<mark>$1</mark>'), esc(text));
}

function cards(hits, terms, emptyMsg) {
  if (!hits.length) return `<p class="empty">${emptyMsg}</p>`;
  return hits.map((h) => `<article class="card">
    <h4>${mark(h.name, terms)}</h4>
    <p class="chips"><span>${esc(h.project)}</span><span>${esc(h.dataset)}</span><span class="alt">${esc(h.split)}</span><span class="mut">#${h.id} · ${h.lines.length} 行</span></p>
    <ol>${h.ev.slice(0, 3).map((e) => `<li><b>L${e.n}</b> ${mark(e.l, terms)}</li>`).join('')}</ol>
  </article>`).join('');
}

const SQL = (q) => `SELECT id, name, project, dataset, split, body,\n       MATCH(body) AGAINST (?) AS score\n  FROM exp_record\n WHERE MATCH(body) AGAINST (?)\n ORDER BY score DESC, id ASC\n LIMIT 20;`;

function render() {
  const q1 = document.getElementById('dq').value;
  const t1 = q1.toLowerCase().split(/[\s,，、;；]+/).filter(Boolean);
  const h1 = find(q1, '');
  document.getElementById('dhits').innerHTML = cards(h1, t1, '索引未命中：换用与记录原文一致的写法，或去掉筛选（本地版会给出同样的提示）。');
  const box = document.getElementById('dsql');
  box.textContent = h1.length
    ? `-- 本地版实际执行的语句（ik 分词 + 自然语言模式，占位符即参数）\n${SQL(q1)}\n-- 传参 ${JSON.stringify([q1, q1])}\n-- EXPLAIN 原文见 docs/seekdb-evidence.md（计划算子 TEXT RETRIEVAL SCAN）`
    : '-- 无命中，本地版此时不返回结果并给出失配提示';

  const p = document.getElementById('dp').value;
  const h2 = find('复现实验', p);
  document.getElementById('pf-hits').innerHTML = cards(h2, ['复现实验'], p ? `筛选「${p}」下没有含「复现实验」的记录 —— 换回「全部」即可看到命中。` : '');
  document.getElementById('pf-hits').insertAdjacentHTML('beforeend', `<p class="mut">当前条件：项目 = ${p ? esc(p) : '全部'} · 命中 ${h2.length} 条（演示样例 #5 为现场导入的新记录）</p>`);
}

function cmpTable(a, b) {
  const rows = a.metrics.map(([n, va, ua]) => {
    const m = b.metrics.find((x) => x[0] === n);
    if (!m) return `<tr><td>${n}</td><td>${va}${ua}</td><td>—</td><td colspan="2" class="mut">仅单侧存在</td></tr>`;
    const [, vb, ub] = m;
    const unitClash = ua.trim().toLowerCase() !== ub.trim().toLowerCase();
    const d = vb - va;
    return `<tr><td>${n}</td><td>${va}${ua}</td><td>${vb}${ub}</td><td>${unitClash ? '<span class="bad">单位不一致</span>' : (d > 0 ? '+' : '') + Math.round(d * 1000) / 1000}</td><td>${unitClash ? '—' : va ? (Math.round((d / va) * 10000) / 100) + '%' : '—'}</td></tr>`;
  }).join('');
  const clashes = a.metrics.filter((x) => { const m = b.metrics.find((y) => y[0] === x[0]); return m && x[2].trim().toLowerCase() !== m[2].trim().toLowerCase(); });
  const warns = [];
  if (clashes.length) warns.push(`指标单位不一致：${clashes.map((c) => c[0]).join('、')}（${clashes[0][2] || '无单位'} vs 另一侧单位不同），不可直接相减`);
  if (a.dataset !== b.dataset) warns.push(`数据集不同：${a.dataset} ≠ ${b.dataset}，差异可能来自数据而非改动`);
  if (a.split !== b.split) warns.push(`划分不同：${a.split} ≠ ${b.split}`);
  return { rows, warns };
}

function renderCompare() {
  for (const [pair, box, key] of [[[R[0], R[1]], 'cmpA', 'ok'], [[R[1], R[2]], 'cmpB', 'bad']]) {
    const [a, b] = pair;
    const { rows, warns } = cmpTable(a, b);
    document.getElementById(box).innerHTML = `<h4>${key === 'ok' ? '可比：同数据集、同划分、同单位' : '不可比：数据集不同 + accuracy 单位不一致'}</h4>
      <p class="mut">${esc(a.name)}（${esc(a.dataset)} / ${esc(a.split)}） → ${esc(b.name)}（${esc(b.dataset)} / ${esc(b.split)}）</p>
      ${warns.length ? `<ul class="warns">${warns.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : '<p class="good">数据集、划分与指标单位一致，变化列可直接解读。</p>'}
      <table><thead><tr><th>指标</th><th>${esc(a.name.slice(0, 6))}</th><th>${esc(b.name.slice(0, 6))}</th><th>变化</th><th>相对</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  const lineOf = (rec, metric) => { const i = rec.lines.findIndex((l) => l.startsWith(`| ${metric} `)); return i + 1; };
  const a = R[0], b = R[1];
  document.getElementById('dmd').textContent =
    `# 实验对比：${a.name} → ${b.name}\n\n| 指标 | 基线 | 对比 | 变化 | 相对变化 |\n| --- | --- | --- | --- | --- |\n| accuracy | 71.4% | 78.9% | +7.5 | +10.5% |\n| f1 | 0.62 | 0.71 | +0.09 | +14.52% |\n\n## 证据\n- accuracy：${a.name} 第 ${lineOf(a, 'accuracy')} 行 / ${b.name} 第 ${lineOf(b, 'accuracy')} 行\n- f1：${a.name} 第 ${lineOf(a, 'f1')} 行 / ${b.name} 第 ${lineOf(b, 'f1')} 行\n\n> 由实验溯源台「复制 Markdown 摘要」生成（演示样例数据）。`;
}

document.addEventListener('DOMContentLoaded', () => {
  const tabs = [...document.querySelectorAll('[role=tab]')];
  tabs.forEach((t) => t.addEventListener('click', () => {
    tabs.forEach((x) => { x.classList.toggle('on', x === t); x.setAttribute('aria-selected', x === t); document.getElementById(x.getAttribute('aria-controls')).hidden = x !== t; });
  }));
  ['dq', 'dp'].forEach((id) => document.getElementById(id).addEventListener('input', render));
  document.getElementById('dbtn').addEventListener('click', render);
  render();
  renderCompare();
});
