export const MAX_CHUNKS = 100;
export const MAX_FILE_BYTES = 180 * 1024;
export const MAX_IMPORT_BYTES = 320 * 1024;
export const MAX_QUERY_LENGTH = 140;
const CHUNK_LENGTH = 280;
const OVERLAP = 32;

export function splitNote(text) {
  const points = Array.from(text.replace(/\r\n?/g, '\n').trim());
  const chunks = [];
  let offset = 0;
  while (offset < points.length) {
    let end = Math.min(offset + CHUNK_LENGTH, points.length);
    if (end < points.length) {
      for (let boundary = end; boundary > offset + 195; boundary -= 1) {
        if (/[。！？；.!?\n\s]/u.test(points[boundary - 1])) {
          end = boundary;
          break;
        }
      }
    }
    const content = points.slice(offset, end).join('').trim();
    if (content) chunks.push(content);
    offset = end === points.length ? end : end - OVERLAP;
  }
  return chunks;
}

export async function prepareNotes(notes) {
  const encoder = new TextEncoder();
  const chunks = new Map();
  let bytes = 0;
  for (const note of notes) {
    const title = note.title.normalize('NFC').trim().slice(0, 120);
    const text = note.text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').normalize('NFC').trim();
    if (!title || !text) continue;
    const payload = encoder.encode(`${title}\n${text}`);
    if (payload.length > MAX_FILE_BYTES) throw new Error(`《${title}》超过单篇 180 KiB 上限。`);
    bytes += payload.length;
    if (bytes > MAX_IMPORT_BYTES) throw new Error('本批文本超过 320 KiB，请分批导入。');

    const digest = await crypto.subtle.digest('SHA-256', payload);
    const noteId = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
    const parts = splitNote(text);
    parts.forEach((content, index) => {
      const id = `${noteId}-${index + 1}`;
      chunks.set(id, { id, noteId, title, content, part: index + 1, totalParts: parts.length });
    });
    if (chunks.size > MAX_CHUNKS) throw new Error('本批内容超过 100 段，请分批导入。');
  }
  if (!chunks.size) throw new Error('没有可导入的文字。请选择 UTF-8 编码的 .txt 或 .md 文件。');
  return [...chunks.values()];
}
