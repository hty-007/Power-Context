import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import { mkdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { fileURLToPath } from 'node:url';
import { ASSETS, ASSET_SHA256, MODEL } from '../shared/model.mjs';

const ROOT = fileURLToPath(new URL('../.runtime/models/', import.meta.url));
const folder = join(ROOT, MODEL.id);
const manifestPath = join(folder, 'manifest.json');
const previous = await readFile(manifestPath, 'utf8').then(JSON.parse, () => ({}));
const files = {};

async function fingerprint(path) {
  const hash = createHash('sha256');
  for await (const bytes of createReadStream(path)) hash.update(bytes);
  return { bytes: (await stat(path)).size, sha256: hash.digest('hex') };
}

async function fetchAsset(name) {
  const path = join(folder, name);
  await mkdir(dirname(path), { recursive: true });
  const cached = await fingerprint(path).catch(() => null);
  if (cached?.sha256 === ASSET_SHA256[name]) return {
    ...cached, downloadMs: previous.revision === MODEL.revision ? previous.files?.[name]?.downloadMs ?? null : null,
  };

  const url = `https://huggingface.co/${MODEL.id}/resolve/${MODEL.revision}/${name}?download=true`;
  const temporary = `${path}.part`;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const start = performance.now();
      const response = await fetch(url, { signal: AbortSignal.timeout(240_000) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      await pipeline(Readable.fromWeb(response.body), createWriteStream(temporary));
      const result = await fingerprint(temporary);
      if (result.sha256 !== ASSET_SHA256[name]) throw new Error('SHA-256 mismatch');
      if (name.endsWith('.onnx') && result.bytes < 1_000_000) throw new Error('Incomplete ONNX weights');
      if (name.endsWith('.json')) JSON.parse(await readFile(temporary, 'utf8'));
      await rename(temporary, path);
      console.log(`${name}: ${result.bytes} bytes`);
      return { ...result, downloadMs: Math.round(performance.now() - start) };
    } catch (error) {
      await rm(temporary, { force: true });
      if (attempt === 3) throw new Error(`Download ${name}: ${error.message}`, { cause: error });
      await new Promise(resolve => setTimeout(resolve, 1_500 * attempt));
    }
  }
}

await Promise.all(ASSETS.map(async name => { files[name] = await fetchAsset(name); }));
await writeFile(manifestPath, `${JSON.stringify({ id: MODEL.id, revision: MODEL.revision, files }, null, 2)}\n`);
console.log(`Verified model assets at ${folder}`);
