import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../.runtime/', import.meta.url));
process.env.SEEKDB_BINDINGS_CACHE_DIR ??= join(root, 'bindings');
const { SeekdbClient } = await import('seekdb');
const db = new SeekdbClient({ path: join(root, 'db-probe') });
try {
  console.log(await db.execute('SELECT VERSION() AS version'));
} finally {
  await db.close();
}
