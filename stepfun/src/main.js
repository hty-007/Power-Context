import './style.css';
import { api, ingest, query } from './workspace.js';
import { DEFAULT_PRECISION } from '../shared/model.mjs';
import { MAX_FILE_BYTES, MAX_IMPORT_BYTES, MAX_QUERY_LENGTH } from '../shared/notes.mjs';
import { sampleNotes } from '../data/sample.mjs';

const icons = {
  logo: '<path d="M4 5h7a3 3 0 0 1 3 3v11H7a3 3 0 0 0-3 1z"/><path d="M14 8a3 3 0 0 1 3-3h3v15a3 3 0 0 0-3-1h-3M7 9h4m-4 4h4m6-4h1m-1 4h1"/>',
  search: '<circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 4 4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  upload: '<path d="M12 16V5m-4 4 4-4 4 4M5 15v4h14v-4"/>',
  file: '<path d="M6 4h8l4 4v12H6zM14 4v5h4M9 13h6m-6 3h6"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  chips: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M10 10h4v4h-4zM9 3v3m6-3v3M9 18v3m6-3v3M3 9h3m-3 6h3m12-6h3m-3 6h3"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
};

function icon(name, size = 20) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;
}

document.querySelector('#app').innerHTML = `
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand"><div class="brand-mark">${icon('logo', 24)}</div><span>课页</span></div>
      <div class="engine"><i></i><span id="database-label">连接 seekdb</span></div>
    </div>
  </header>
  <main class="layout">
    <aside class="sidebar" aria-label="知识库">
      <div class="aside-heading"><h2>知识库</h2><button id="new-library" class="icon-button" type="button" title="新建知识库" aria-label="新建知识库">${icon('plus', 19)}</button></div>
      <nav id="libraries" class="libraries" aria-label="知识库列表"></nav>
      <section class="import-panel" aria-label="导入笔记">
        <h2>导入笔记</h2>
        <label id="drop-zone" class="drop-zone" for="note-files" tabindex="0">
          ${icon('upload', 22)}
          <span>选取文件或拖到这里</span><small>UTF-8 · TXT / Markdown</small>
        </label>
        <input type="file" id="note-files" accept=".txt,.md,.markdown,text/plain,text/markdown" multiple hidden />
        <button id="import-demo" type="button" class="subtle-button">${icon('file', 17)} 导入示例笔记</button>
      </section>
      <div class="runtime-panel">
        <div class="runtime-icon">${icon('chips', 18)}</div>
        <div><span class="runtime-title" id="runtime-label">等待本地推理</span><span class="runtime-detail" id="runtime-detail">BGE Small · FP16</span></div>
      </div>
    </aside>
    <section class="content" aria-labelledby="page-title">
      <header class="content-heading"><h1 id="page-title">课程笔记</h1><span class="count" id="chunk-count">0 段笔记</span></header>
      <form id="search-form" class="search-panel">
        <label for="query" class="sr-only">搜索笔记</label>
        <div class="search-box">${icon('search', 23)}<input id="query" type="search" required maxlength="${MAX_QUERY_LENGTH}" placeholder="输入关键词或一个问题" autocomplete="off" /><button id="search-button" class="primary-button" type="submit">搜索 ${icon('arrow', 18)}</button></div>
        <div class="search-options">
          <fieldset class="segmented"><legend class="sr-only">检索方式</legend><label><input type="radio" name="mode" value="hybrid" checked /><span>混合检索</span></label><label><input type="radio" name="mode" value="semantic" /><span>语义检索</span></label></fieldset>
          <div class="examples" id="examples"><button type="button">二级索引为什么要回表？</button><button type="button">TCP 第三个 ACK 有什么用？</button></div>
        </div>
      </form>
      <div id="status" class="status" role="status" aria-live="polite" hidden><span></span><progress id="progress" max="100" hidden></progress></div>
      <div id="results" class="results" aria-live="polite"></div>
    </section>
  </main>
  <dialog id="library-dialog">
    <form id="library-form" class="library-form">
      <div class="dialog-heading"><h2>新建知识库</h2><button type="button" id="close-dialog" class="icon-button" aria-label="关闭">${icon('close')}</button></div>
      <label for="library-name">名称</label><input id="library-name" type="text" required maxlength="36" placeholder="例如：计算机网络" />
      <button type="submit" class="primary-button">创建</button>
    </form>
  </dialog>`;

const el = id => document.getElementById(id);
const selectedKey = 'course-notes-selected-library';
let current;
let libraries = [];
let locked = false;
let count = 0;

function element(tag, className, text) {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== undefined) value.textContent = text;
  return value;
}

function message(text, tone = 'neutral', percent = null) {
  const box = el('status');
  box.hidden = !text;
  box.dataset.tone = tone;
  box.firstElementChild.textContent = text;
  el('progress').hidden = percent === null;
  if (percent !== null) el('progress').value = percent;
}

function modelStatus({ device, precision, fallbackReason }) {
  if (!device) return;
  el('runtime-label').textContent = device === 'webgpu' ? 'WebGPU · 浏览器推理' : 'WASM · 浏览器推理';
  const fallback = fallbackReason || (device === 'wasm' ? el('runtime-detail').dataset.fallback : '');
  el('runtime-detail').dataset.fallback = fallback || '';
  el('runtime-detail').textContent = `BGE Small · ${precision.toUpperCase()}${fallback ? ' · 已自动降级' : ''}`;
  el('runtime-detail').title = fallback || '';
}

function onProgress(event) {
  const { phase, device, progress, complete, total, fallbackReason } = event;
  if (phase === 'fallback') modelStatus(event);
  const engine = device === 'webgpu' ? 'WebGPU' : 'WASM';
  if (phase === 'prepare') message('读取笔记并检查重复内容…');
  if (phase === 'load') message(fallbackReason ? 'WebGPU 不可用，正在切换到 WASM…' : `正在初始化 ${engine} 模型…`);
  if (phase === 'download') message(`正在读取模型文件 · ${progress}%`, 'neutral', progress);
  if (phase === 'embed') {
    modelStatus(event);
    message(`浏览器正在向量化 · ${complete} / ${total} 段`, 'neutral', complete / total * 100);
  }
  if (phase === 'write') message('正在写入 seekdb 并刷新索引…');
  if (phase === 'search') message('seekdb 正在检索…');
}

function setBusy(value) {
  locked = value;
  for (const id of ['new-library', 'note-files', 'import-demo', 'search-button']) el(id).disabled = value;
  for (const button of document.querySelectorAll('#libraries button, #examples button')) button.disabled = value;
  el('results').setAttribute('aria-busy', String(value));
}

async function perform(action) {
  if (locked) return;
  setBusy(true);
  try {
    await action();
  } catch (error) {
    message(error.message || '操作未完成，请重试。', 'error');
  } finally {
    setBusy(false);
  }
}

function emptyState() {
  const box = element('div', 'empty');
  const symbol = element('div', 'empty-icon');
  symbol.innerHTML = icon(count ? 'search' : 'logo', 30);
  box.append(symbol, element('h2', '', count ? '准备好搜索' : '知识库还是空的'));
  box.append(element('p', '', count ? '输入一段记得的内容，或描述你想弄懂的问题。' : '导入自己的笔记，或先用示例体验。'));
  el('results').replaceChildren(box);
}

function showCount() {
  el('chunk-count').textContent = `${count} 段笔记`;
}

async function selectLibrary(id) {
  const selected = await api(`/libraries/${id}`);
  current = selected;
  count = selected.chunks;
  localStorage.setItem(selectedKey, selected.id);
  el('page-title').textContent = selected.name;
  el('runtime-label').textContent = '等待本地推理';
  el('runtime-detail').textContent = `BGE Small · ${selected.precision.toUpperCase()}`;
  el('runtime-detail').dataset.fallback = '';
  el('runtime-detail').title = '';
  el('query').value = '';
  showCount();
  const buttons = libraries.map(library => {
    const button = element('button', 'library-button');
    button.type = 'button';
    button.disabled = locked;
    button.dataset.library = library.id;
    if (library.id === selected.id) button.setAttribute('aria-current', 'true');
    button.innerHTML = icon('logo', 18);
    button.append(element('span', '', library.name));
    button.addEventListener('click', () => perform(() => selectLibrary(library.id)));
    return button;
  });
  el('libraries').replaceChildren(...buttons);
  message('');
  emptyState();
}

async function addNotes(notes) {
  if (!current) throw new Error('知识库未加载，请刷新页面。');
  const outcome = await ingest(current, notes, {}, onProgress);
  modelStatus(outcome);
  count = outcome.chunks;
  showCount();
  emptyState();
  const duplicate = outcome.duplicates ? `，跳过 ${outcome.duplicates} 段重复内容` : '';
  message(`已新增 ${outcome.inserted} 段笔记${duplicate}。`, 'success');
}

async function importFiles(files) {
  if (!files.length) return;
  if (files.some(file => !/\.(txt|md|markdown)$/i.test(file.name))) throw new Error('请选择 .txt 或 .md 文件。');
  if (files.some(file => file.size > MAX_FILE_BYTES)) throw new Error('单篇文件最大 180 KiB。');
  if (files.reduce((sum, file) => sum + file.size, 0) > MAX_IMPORT_BYTES) throw new Error('本批文件超过 320 KiB，请分批导入。');
  const decoder = new TextDecoder('utf-8', { fatal: true });
  const notes = await Promise.all(files.map(async file => {
    const bytes = await file.arrayBuffer();
    try {
      return { title: file.name.replace(/\.(txt|md|markdown)$/i, ''), text: decoder.decode(bytes) };
    } catch {
      throw new Error(`《${file.name}》不是有效的 UTF-8 文件。`);
    }
  }));
  await addNotes(notes);
}

function showResults(response, text) {
  const top = element('div', 'result-heading');
  top.append(element('h2', '', `找到 ${response.rows.length} 条结果`));
  top.append(element('span', '', `推理 ${Math.round(response.inferenceMs)} ms · seekdb ${Math.round(response.dbMs)} ms`));
  const resultList = response.rows.map((row, index) => {
    const card = element('article', 'result-card');
    const position = element('span', 'rank', String(index + 1));
    const textBlock = element('div', 'result-copy');
    textBlock.append(element('h3', '', row.title), element('p', 'excerpt', row.content));
    const footer = element('span', 'source', `第 ${row.part} / ${row.totalParts} 段`);
    textBlock.append(footer);
    card.append(position, textBlock);
    return card;
  });
  if (!resultList.length) resultList.push(element('p', 'no-results', `没有找到与“${text}”相关的笔记。`));
  el('results').replaceChildren(top, ...resultList);
}

el('search-form').addEventListener('submit', event => {
  event.preventDefault();
  perform(async () => {
    if (!current || !count) throw new Error('先导入笔记，再开始搜索。');
    const text = el('query').value.trim();
    if (!text) throw new Error('请输入要找的内容。');
    const mode = new FormData(event.currentTarget).get('mode');
    const result = await query(current, text, mode, {}, onProgress);
    modelStatus(result);
    showResults(result, text);
    message('');
  });
});

el('examples').addEventListener('click', event => {
  if (locked || event.target.tagName !== 'BUTTON') return;
  el('query').value = event.target.textContent;
  el('search-form').requestSubmit();
});

el('note-files').addEventListener('change', event => {
  const files = [...event.target.files];
  event.target.value = '';
  perform(() => importFiles(files));
});
el('import-demo').addEventListener('click', () => perform(() => addNotes(sampleNotes)));
const drop = el('drop-zone');
drop.addEventListener('keydown', event => {
  if (!locked && (event.key === 'Enter' || event.key === ' ')) {
    event.preventDefault();
    el('note-files').click();
  }
});
drop.addEventListener('dragover', event => {
  event.preventDefault();
  if (!locked) drop.classList.add('dragging');
});
drop.addEventListener('dragleave', () => drop.classList.remove('dragging'));
drop.addEventListener('drop', event => {
  event.preventDefault();
  drop.classList.remove('dragging');
  const files = [...event.dataTransfer.files];
  perform(() => importFiles(files));
});

el('new-library').addEventListener('click', () => {
  el('library-form').reset();
  el('library-dialog').showModal();
  el('library-name').focus();
});
el('close-dialog').addEventListener('click', () => el('library-dialog').close());
el('library-form').addEventListener('submit', event => {
  event.preventDefault();
  const name = el('library-name').value.trim();
  el('library-dialog').close();
  perform(async () => {
    const library = await api('/libraries', { name, precision: DEFAULT_PRECISION });
    libraries = await api('/libraries');
    await selectLibrary(library.id);
  });
});

if (new URLSearchParams(location.search).has('acceptance')) window.courseNotesAcceptance = { api, ingest, query };

perform(async () => {
  const service = await api('/status');
  el('database-label').textContent = 'seekdb 已连接';
  el('database-label').title = service.version;
  el('database-label').parentElement.classList.add('connected');
  libraries = await api('/libraries');
  let initial = libraries.find(library => library.name === '课程笔记' && library.precision === DEFAULT_PRECISION);
  if (!initial) {
    initial = await api('/libraries', { name: '课程笔记', precision: DEFAULT_PRECISION });
    libraries = await api('/libraries');
  }
  const remembered = libraries.find(library => library.id === localStorage.getItem(selectedKey));
  await selectLibrary((remembered ?? initial).id);
});
