import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const project = 'issue-list';
export const fixture = fileURLToPath(new URL('../fixture/issue-list/', import.meta.url));
export const sha256 = value => createHash('sha256').update(value).digest('hex');
const read = async path => (await readFile(join(fixture, path), 'utf8')).replace(/\r\n/g, '\n');
export const tasks = JSON.parse(await read('tasks.json'));
const manifest = JSON.parse(await read('sources.json'));

function extract(text, section) {
  const lines = text.trimEnd().split('\n');
  const start = section ? lines.indexOf(`## ${section}`) : 0;
  if (start < 0) throw new Error(`Missing section: ${section}`);
  const next = section ? lines.findIndex((line, i) => i > start && line.startsWith('## ')) : -1;
  const selected = lines.slice(start, next === -1 ? lines.length : next);
  while (selected.at(-1) === '') selected.pop();
  return { startLine: start + 1, endLine: start + selected.length, content: selected.join('\n') };
}

export async function fixtureSources(revision) {
  if (!['v1', 'v2'].includes(revision)) throw new Error('Specify v1 or v2.');
  return Promise.all(manifest.map(async entry => {
    const path = revision === 'v2' && entry.v2Path ? entry.v2Path : entry.path;
    const excerpt = extract(await read(path), entry.section);
    if (entry.symbol && !excerpt.content.includes(`function ${entry.symbol}(`)) {
      throw new Error(`Missing symbol ${entry.symbol} in ${path}`);
    }
    return {
      id: entry.id, kind: entry.kind, path, symbol: entry.symbol ?? null, tasks: entry.tasks,
      ...excerpt, sha256: sha256(excerpt.content),
    };
  }));
}
