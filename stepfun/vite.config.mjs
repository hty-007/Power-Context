import { copyFile, mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';
import { defineConfig } from 'vite';

const require = createRequire(import.meta.url);
const transformers = dirname(require.resolve('@huggingface/transformers'));

export default defineConfig({
  build: { target: 'es2022' },
  worker: { format: 'es' },
  plugins: [{
    name: 'serve-onnx-runtime-locally',
    async closeBundle() {
      const output = join('dist', 'ort');
      await mkdir(output, { recursive: true });
      await Promise.all(['mjs', 'wasm'].map(extension => {
        const name = `ort-wasm-simd-threaded.jsep.${extension}`;
        return copyFile(join(transformers, name), join(output, name));
      }));
    },
  }],
});
