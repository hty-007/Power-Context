import { env, pipeline } from '@huggingface/transformers';
import { MODEL } from '../shared/model.mjs';

env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = `/models/${MODEL.revision}/`;
env.useBrowserCache = false;
env.backends.onnx.wasm.wasmPaths = new URL('/ort/', self.location.origin).href;
env.backends.onnx.wasm.numThreads = self.crossOriginIsolated
  ? Math.min(4, self.navigator.hardwareConcurrency || 2)
  : 1;

let active;
const fallbackByPrecision = new Map();
let queue = Promise.resolve();

async function resolveGPU() {
  const adapter = await self.navigator.gpu?.requestAdapter({ powerPreference: 'high-performance' });
  if (!adapter) throw new Error('浏览器未提供 WebGPU 适配器。');
  return {
    vendor: adapter.info?.vendor || '',
    architecture: adapter.info?.architecture || '',
    description: adapter.info?.description || '',
    fallback: adapter.info?.isFallbackAdapter ?? false,
  };
}

async function getModel(device, precision, emit) {
  if (active?.device === device && active.precision === precision) {
    return { extractor: active.extractor, adapter: active.adapter, loadMs: 0, transferMs: 0, transferBytes: 0 };
  }
  const adapter = device === 'webgpu' ? await resolveGPU() : null;
  if (active) {
    const previous = active;
    active = undefined;
    await previous.extractor.dispose();
  }
  const start = performance.now();
  const extractor = await pipeline('feature-extraction', MODEL.id, {
    device,
    dtype: precision,
    local_files_only: true,
    progress_callback(info) {
      if (info.status === 'progress' && info.file?.endsWith('.onnx')) {
        emit({ phase: 'download', progress: Math.round(info.progress), device, precision });
      }
    },
  });
  const loadMs = performance.now() - start;
  const resources = performance.getEntriesByType('resource').filter(entry => (
    entry.startTime >= start && entry.name.includes(`/models/${MODEL.revision}/`)
  ));
  const transferMs = resources.length ? Math.max(...resources.map(entry => entry.responseEnd)) - Math.min(...resources.map(entry => entry.startTime)) : 0;
  const transferBytes = resources.reduce((total, entry) => total + entry.transferSize, 0);
  active = { extractor, adapter, device, precision };
  return { extractor, adapter, loadMs, transferMs, transferBytes };
}

async function embed({ texts, precision, preference, batchSize }, emit) {
  let fallbackReason = preference === 'auto' ? fallbackByPrecision.get(precision) || '' : '';
  const devices = preference === 'auto' ? (fallbackReason ? ['wasm'] : ['webgpu', 'wasm']) : [preference];
  for (const device of devices) {
    try {
      emit({ phase: 'load', device, precision, fallbackReason });
      const { extractor, adapter, loadMs, transferMs, transferBytes } = await getModel(device, precision, emit);
      const vectors = [];
      let inferenceMs = 0;
      for (let i = 0; i < texts.length; i += batchSize) {
        const input = texts.slice(i, i + batchSize);
        const start = performance.now();
        const output = await extractor(input, { pooling: 'cls', normalize: true });
        inferenceMs += performance.now() - start;
        for (const vector of output.tolist()) {
          if (vector.length !== MODEL.dimension || !vector.every(Number.isFinite)) {
            throw new Error('模型返回了无效的向量。');
          }
          vectors.push(vector);
        }
        emit({ phase: 'embed', complete: vectors.length, total: texts.length, device, precision, fallbackReason });
      }
      return {
        vectors, device, precision, adapter, fallbackReason, loadMs, transferMs, transferBytes,
        inferenceMs, batchSize, wasmThreads: device === 'wasm' ? env.backends.onnx.wasm.numThreads : null,
      };
    } catch (error) {
      if (device !== 'webgpu' || preference !== 'auto') throw error;
      fallbackReason = error.message || String(error);
      fallbackByPrecision.set(precision, fallbackReason);
      if (active?.device === 'webgpu') {
        const previous = active;
        active = undefined;
        await previous.extractor.dispose().catch(() => {});
      }
      emit({ phase: 'fallback', device: 'wasm', precision, fallbackReason });
    }
  }
}

self.addEventListener('message', event => {
  const { id, ...options } = event.data;
  queue = queue.then(async () => {
    try {
      const result = await embed(options, progress => self.postMessage({ id, progress }));
      self.postMessage({ id, result });
    } catch (error) {
      self.postMessage({ id, error: error.message || String(error) });
    }
  });
});
