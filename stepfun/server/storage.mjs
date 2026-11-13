import { createHash } from 'node:crypto';
import { FulltextIndexConfig, Schema, SeekdbClient, VectorIndexConfig } from 'seekdb';
import { MODEL } from '../shared/model.mjs';

const WINDOW = 25;
const libraryTable = 'course_notes_libraries';

export function createStorage(serverOptions) {
  const client = new SeekdbClient(serverOptions);
  const collections = new Map();
  let tail = Promise.resolve();

  function sequential(operation) {
    const next = tail.then(operation);
    tail = next.catch(() => {});
    return next;
  }

  async function initialize() {
    const [engine] = await client.execute('SELECT VERSION() AS version');
    await client.execute(`CREATE TABLE IF NOT EXISTS ${libraryTable} (
      id VARCHAR(40) PRIMARY KEY,
      name VARCHAR(100) NOT NULL,
      vector_dtype VARCHAR(8) NOT NULL,
      created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    )`);
    return engine.version;
  }

  async function listLibraries() {
    const rows = await client.execute(`SELECT id, name, vector_dtype FROM ${libraryTable} ORDER BY created_at, id`);
    return rows.map(({ vector_dtype, ...row }) => ({ ...row, precision: vector_dtype }));
  }

  async function getLibrary(id) {
    const [library] = await client.execute(`SELECT id, name, vector_dtype FROM ${libraryTable} WHERE id = ?`, [id]);
    if (!library) throw Object.assign(new Error('找不到这个知识库。'), { status: 404 });
    const { vector_dtype, ...row } = library;
    return { ...row, precision: vector_dtype };
  }

  async function createLibrary(name, precision) {
    const id = `notes_${createHash('sha256').update(`${name}\n${precision}`).digest('hex').slice(0, 24)}`;
    const existing = (await listLibraries()).find(library => library.id === id);
    if (existing) return existing;
    await getCollection(id);
    await client.execute(`INSERT INTO ${libraryTable} (id, name, vector_dtype) VALUES (?, ?, ?)`, [id, name, precision]);
    return { id, name, precision };
  }

  async function getCollection(id) {
    if (!collections.has(id)) {
      const schema = new Schema({
        fulltextIndex: new FulltextIndexConfig('ik', { ik_mode: 'smart' }),
        vectorIndex: new VectorIndexConfig({
          hnsw: { dimension: MODEL.dimension, distance: 'cosine' }, embeddingFunction: null,
        }),
      });
      const collection = await client.getOrCreateCollection({ name: id, schema, embeddingFunction: null });
      collections.set(id, collection);
    }
    return collections.get(id);
  }

  async function state(id) {
    const library = await getLibrary(id);
    const collection = await getCollection(id);
    return { ...library, chunks: Number(await collection.count()) };
  }

  async function existing(id, ids) {
    await getLibrary(id);
    const collection = await getCollection(id);
    return (await collection.get({ ids, include: ['metadatas'] })).ids;
  }

  async function index(id, precision, chunks) {
    const library = await getLibrary(id);
    if (precision !== library.precision) throw Object.assign(new Error('笔记向量精度与知识库不一致。'), { status: 409 });
    const collection = await getCollection(id);
    const start = performance.now();
    const known = await existing(id, chunks.map(chunk => chunk.id));
    const knownIds = new Set(known);
    const fresh = chunks.filter(chunk => !knownIds.has(chunk.id));

    for (let offset = 0; offset < fresh.length; offset += 24) {
      const batch = fresh.slice(offset, offset + 24);
      await collection.add({
        ids: batch.map(chunk => chunk.id),
        documents: batch.map(chunk => `${chunk.title}\n${chunk.content}`),
        embeddings: batch.map(chunk => chunk.vector),
        metadatas: batch.map(({ title, noteId, part, totalParts }) => ({ title, noteId, part, totalParts })),
      });
    }

    const writeMs = performance.now() - start;
    const refreshStarted = performance.now();
    await collection.refresh_index();
    const committed = await existing(id, chunks.map(chunk => chunk.id));
    if (committed.length !== chunks.length) throw new Error('seekdb 回查的记录数与本次导入不一致。');
    return {
      inserted: fresh.length, duplicates: known.length, chunks: Number(await collection.count()),
      writeMs, refreshMs: performance.now() - refreshStarted,
    };
  }

  async function search(id, precision, text, vector, mode) {
    const library = await getLibrary(id);
    if (precision !== library.precision) throw Object.assign(new Error('查询向量精度与知识库不一致。'), { status: 409 });
    const collection = await getCollection(id);
    const started = performance.now();
    const result = mode === 'hybrid'
      ? await collection.hybridSearch({
        query: { whereDocument: { $contains: text }, nResults: WINDOW },
        knn: { queryEmbeddings: vector, nResults: WINDOW },
        rank: { rrf: { rankWindowSize: WINDOW, rankConstant: 20 } },
        nResults: 10,
        include: ['documents', 'metadatas'],
      })
      : await collection.query({ queryEmbeddings: [vector], nResults: 10, include: ['documents', 'metadatas'] });
    const dbMs = performance.now() - started;
    const rows = (result.ids[0] ?? []).map((chunkId, i) => {
      const meta = result.metadatas?.[0]?.[i];
      const document = result.documents?.[0]?.[i] ?? '';
      return { id: chunkId, ...meta, content: document.slice(document.indexOf('\n') + 1) };
    });
    return { rows, dbMs, mode };
  }

  return { initialize, sequential, listLibraries, createLibrary, state, existing, index, search, close: () => client.close() };
}
