import assert from 'node:assert/strict';
import test from 'node:test';
import { prepareNotes, splitNote } from '../shared/notes.mjs';

test('normalizes line endings and recognizes duplicate imports', async () => {
  const first = await prepareNotes([{ title: '数据库', text: '\uFEFF同一篇\r\n笔记' }]);
  const both = await prepareNotes([
    { title: ' 数据库 ', text: '同一篇\n笔记' },
    { title: '数据库', text: '同一篇\r\n笔记' },
  ]);
  assert.deepEqual(both, first);
  const revised = await prepareNotes([{ title: '数据库', text: '更新了一篇笔记' }]);
  assert.notEqual(revised[0].noteId, first[0].noteId);
});

test('chunks long notes without splitting Unicode code points', () => {
  const note = `${'😀'.repeat(270)}。${'事务笔记。'.repeat(100)}`;
  const chunks = splitNote(note);
  assert.ok(chunks.length > 1);
  assert.ok(chunks.every(chunk => Array.from(chunk).length <= 280 && !/\p{Surrogate}/u.test(chunk)));
  assert.ok(chunks[1].startsWith(Array.from(chunks[0]).slice(-32).join('')));
});

test('rejects empty notes and requests beyond the chunk limit', async () => {
  await assert.rejects(prepareNotes([{ title: '空笔记', text: ' ' }]), /没有可导入/);
  await assert.rejects(prepareNotes([{ title: '长笔记', text: '字'.repeat(30_000) }]), /超过 100 段/);
});
