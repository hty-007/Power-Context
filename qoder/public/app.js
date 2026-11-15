'use strict';
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const api = async (p, opts) => {
  const r = await fetch(p, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
};

let RECORDS = [];
let TERMS = [];

function highlight(text) {
  const terms = TERMS.filter(Boolean).sort((a, b) => b.length - a.length);
  if (!terms.length) return esc(text);
  const pattern = terms.map(term => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
  return String(text ?? '').split(new RegExp(`(${pattern})`, 'gi'))
    .map((part, index) => index % 2 ? `<mark>${esc(part)}</mark>` : esc(part))
    .join('');
}

function chips(o) {
  return `<span class="chip">${esc(o.project)}</span><span class="chip">${esc(o.dataset)}</span><span class="chip alt">${esc(o.split)}</span>`;
}

async function boot() {
  const h = await api('/api/health');
  $('#status').innerHTML = `seekdb 已连接 · ${esc(h.transport)} · FULLTEXT ${esc(h.ftParser)} · ${h.stats.records} 份记录 · ${esc(h.charset)}`;
  $('#status').title = `${h.ftIndexDdl}\n服务端 ${h.serverVersion} · 数据目录 ${h.dataDir}${h.ftInitError ? `\n${h.ftInitError}` : ''}`;
  if (h.demoNew) $('#status').innerHTML += ` · 新导入 ${h.demoNew} 份演示样例`;
  else if (h.demoExisting) $('#status').innerHTML += ` · ${h.demoExisting} 份演示样例已在库中`;
  await Promise.all([loadFilters(), loadRecords()]);
  renderSamples();
}

async function loadFilters() {
  const f = await api('/api/filters');
  for (const [sel, list] of [['#f-project', f.projects], ['#f-dataset', f.datasets]]) {
    const el = $(sel);
    el.length = 0;
    el.append(new Option('全部', ''));
    for (const it of list) el.append(new Option(`${it.value}（${it.count}）`, it.value));
  }
}

async function loadRecords() {
  const { records } = await api('/api/records');
  RECORDS = records;
  $('#rec-count').textContent = `${records.length} 份`;
  $('#records').innerHTML = `<thead><tr><th>记录</th><th>项目</th><th>数据集 / 划分</th><th>指标</th><th>导入时间</th></tr></thead><tbody>${
    records.map((r) => `<tr><td><b>${esc(r.name)}</b><br><span class="mut">${esc(r.source || '')}</span></td><td>${esc(r.project)}</td><td>${esc(r.dataset)}<br><span class="mut">${esc(r.split)}</span></td><td>${r.metric_count}</td><td class="mut">${esc(new Date(r.imported_at).toLocaleString('zh-CN'))}</td></tr>`).join('')
  }</tbody>`;
  for (const sel of ['#cmp-a', '#cmp-b']) {
    const el = $(sel);
    el.length = 0;
    for (const r of RECORDS) el.append(new Option(`${r.name}（${r.dataset}）`, r.id));
  }
  if (RECORDS.length > 1) { $('#cmp-a').value = RECORDS[0].id; $('#cmp-b').value = RECORDS[1].id; }
}

function renderSamples() {
  const box = $('#sample-buttons');
  box.innerHTML = '';
  for (const r of RECORDS.filter((x) => (x.source || '').startsWith('samples/'))) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ghost sm';
    b.textContent = `再导入 ${r.source}`;
    b.onclick = () => doImport({ sample: r.source.replace(/^samples\//, '') }, `重复导入 ${r.source}`);
    box.append(b);
  }
}

async function runSearch() {
  const q = $('#q').value.trim();
  const p = new URLSearchParams({ q, project: $('#f-project').value, dataset: $('#f-dataset').value });
  const r = await api(`/api/search?${p}`);
  // 高亮用后端返回的定位词（原词 + ik 实际词元），否则部分词元命中的行会显示成「无高亮」，看起来像没匹配上
  TERMS = r.meta.terms?.length ? r.meta.terms : q.split(/[\s,，、;；]+/).filter(Boolean);
  $('#hits-count').textContent = `${r.hits.length} 条`;
  const note = $('#search-note');
  note.textContent = r.meta.note || '';
  note.hidden = !r.meta.note;
  note.classList.toggle('warn', r.meta.mode !== 'fulltext');
  $('#sqlbox').textContent = r.meta.sql
    ? `-- 模式 ${r.meta.mode}（ik 分词 + 自然语言 MATCH，参数化传参：SQL 里不含检索词原文）\n${r.meta.sql}\n-- 传参 ${JSON.stringify(r.meta.params)}`
      + (r.meta.tokens?.length ? `\n-- 该查询的 ik 词元 SELECT TOKENIZE(?, 'ik') → ${JSON.stringify(r.meta.tokens)}` : '')
      + (r.meta.tokenizeError ? `\n-- 词元获取失败，已退回按原词定位：${r.meta.tokenizeError}` : '')
      + `\n\n-- EXPLAIN 实际执行计划\n${JSON.stringify(r.meta.explain ?? { error: r.meta.explainError }, null, 2)}`
    : `-- ${r.meta.note}`;
  $('#hits').innerHTML = r.hits.length ? r.hits.map(hitCard).join('')
    : `<p class="mut">${r.meta.mode === 'miss' ? '索引未命中，见上方提示。' : '暂无结果。'}</p>`;
}

function hitCard(h) {
  return `<article class="card" tabindex="0">
  <header><h3>${highlight(h.name)}</h3><p>${chips(h)}<span class="mut">记录 #${h.id} · ${h.lineCount} 行 · 相关度 ${h.score.toFixed(3)}</span></p></header>
  <ol class="evi">${h.evidence.map((e) => `<li><span class="ln">L${e.line}</span><span>${highlight(e.text)}</span></li>`).join('') || '<li class="mut">未定位到含原词或实际词元的行，请展开正文核对（原因见上方提示）</li>'}</ol>
  <footer><button type="button" class="ghost sm" data-a="${h.id}">设为基线</button><button type="button" class="ghost sm" data-b="${h.id}">设为对比项</button></footer>
</article>`;
}

async function runCompare() {
  const a = $('#cmp-a').value, b = $('#cmp-b').value;
  if (!a || !b || a === b) { $('#cmp-issues').innerHTML = '<p class="warn">请选择两条不同的记录。</p>'; return; }
  const c = await api(`/api/compare?a=${a}&b=${b}`);
  $('#cmp-issues').innerHTML = c.issues.map((i) => `<p class="warn">⚠ ${esc(i.message)}</p>`).join('')
    + (c.comparable ? '<p class="ok">可比：数据集、划分与指标单位一致。</p>' : '<p class="warn">结论：差异不等同于改动效果，请谨慎解读。</p>');
  const row = (r) => `<tr><td>${esc(r.metric)}</td><td>${r.a === null ? '—' : esc(r.a) + esc(r.unitA || '')}</td><td>${r.b === null ? '—' : esc(r.b) + esc(r.unitB || '')}</td>
    <td>${r.delta === null ? '—' : (r.delta > 0 ? '+' : '') + r.delta}</td><td>${r.pct === null ? '—' : (r.pct > 0 ? '+' : '') + r.pct + '%'}</td>
    <td>${esc(r.direction || '')}${r.comparable === false ? `<br><span class="mut">${esc(r.deltaNote)}</span>` : ''}</td>
    <td class="mut">${r.lineA ? 'L' + r.lineA : ''}${r.lineA && r.lineB ? ' / ' : ''}${r.lineB ? 'L' + r.lineB : ''}</td></tr>`;
  $('#cmp-table').innerHTML = `<thead><tr><th>指标</th><th>${esc(c.records[0].name)}</th><th>${esc(c.records[1].name)}</th><th>变化</th><th>相对</th><th>方向</th><th>证据行</th></tr></thead><tbody>${c.rows.map(row).join('')}</tbody>`;
  $('#cmp-copy').disabled = false;
  $('#cmp-copy').onclick = async () => {
    await navigator.clipboard.writeText(c.markdown);
    $('#cmp-copy').textContent = '已复制（含证据行号）';
    setTimeout(() => { $('#cmp-copy').textContent = '复制 Markdown 摘要'; }, 2000);
  };
}

async function doImport(payload, label) {
  const res = await api('/api/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const box = $('#import-result');
  if (res.duplicate) {
    box.className = 'import-result warn';
    box.textContent = `重复导入已识别：内容指纹与记录 #${res.id}「${res.name}」相同，未新增副本。`;
  } else {
    box.className = 'import-result ok';
    box.textContent = `已导入记录 #${res.id}「${res.parsed.name}」：项目 ${res.parsed.project}，数据集 ${res.parsed.dataset}，划分 ${res.parsed.split}，指标 ${res.parsed.metrics.length} 项。`;
  }
  await Promise.all([loadFilters(), loadRecords()]);
  renderSamples();
  if (label) box.setAttribute('title', label);
}

$('#search-form').addEventListener('submit', (e) => { e.preventDefault(); runSearch().catch((x) => alert(x.message)); });
$('#f-project').addEventListener('change', () => $('#q').value.trim() && runSearch());
$('#f-dataset').addEventListener('change', () => $('#q').value.trim() && runSearch());
$('#cmp-run').addEventListener('click', () => runCompare().catch((x) => alert(x.message)));
$('#do-import').addEventListener('click', () => {
  const md = $('#md').value;
  if (!md.trim()) return alert('请粘贴 Markdown 内容');
  doImport({ markdown: md, source: '手动粘贴' }).catch((x) => alert(x.message));
});
$('#toggle-sql').addEventListener('click', () => {
  const b = $('#sqlbox');
  b.hidden = !b.hidden;
  $('#toggle-sql').textContent = b.hidden ? '查看检索 SQL / EXPLAIN' : '收起检索 SQL / EXPLAIN';
});
$('#pick-file').addEventListener('click', () => $('#file').click());
$('#file').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const text = await f.text();
  $('#md').value = text;
  doImport({ markdown: text, source: f.name }).catch((x) => alert(x.message));
});
$('#hits').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-a],button[data-b]');
  if (!b) return;
  const key = b.dataset.a ? '#cmp-a' : '#cmp-b';
  $(key).value = b.dataset.a || b.dataset.b;
  runCompare();
  document.querySelector('#cmp-h').scrollIntoView({ behavior: 'smooth', block: 'center' });
});
document.addEventListener('keydown', (e) => {
  if (e.key === '/' && document.activeElement !== $('#q') && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
    e.preventDefault();
    $('#q').focus();
  } else if (e.key === 'Escape') { $('#q').value = ''; $('#q').blur(); }
});

boot().catch((e) => { $('#status').textContent = '启动失败：' + e.message; });
