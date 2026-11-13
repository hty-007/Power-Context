import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createReadStream } from 'node:fs';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { sha256 } from '../src/fixture.mjs';
import { readModelCode } from '../src/prompt.mjs';

const model = 'MiniMaxAI/SynLogic-7B';
const upstreamModelRevision = 'bfcdfe2571549f8c40f1169cd62221abed5a5cbe';
const weights = 'SynLogic-7B-Q3_K_M.gguf';
const weightsSha256 = '21be91c718f3909a7faa4a4705e432bc477bfec9a687a698ff5e2b5f9dab8e72';
const weightsRevision = 'a05e87469d413a5a433358e90880e102184ba5a3';
const [promptFile, directory] = process.argv.slice(2);
if (!promptFile || !directory || !process.env.LLAMA_SERVER_EXE || !process.env.SYNLOGIC_GGUF) {
  throw new Error('Set LLAMA_SERVER_EXE and SYNLOGIC_GGUF. Usage: node scripts/request-model.mjs <prompt.txt> <output-directory>');
}

const port = Number(process.env.SDD_MODEL_PORT || 18867);
const origin = `http://127.0.0.1:${port}`;
const gpuLayers = process.env.SDD_MODEL_GPU_LAYERS || '0';
const device = process.env.SDD_MODEL_DEVICE;
const gguf = resolve(process.env.SYNLOGIC_GGUF);
const version = spawnSync(process.env.LLAMA_SERVER_EXE, ['--version'], { encoding: 'utf8', windowsHide: true, timeout: 10_000 });
assert.equal(version.status, 0, 'Cannot run llama-server --version.');
const build = `${version.stdout}\n${version.stderr}`.match(/version:[^\r\n]+/)?.[0];
const hash = createHash('sha256');
for await (const data of createReadStream(gguf)) hash.update(data);
assert.equal(hash.digest('hex'), weightsSha256, 'Quantized model SHA-256 mismatch.');
const prompt = await readFile(promptFile, 'utf8');
const messages = [{ role: 'user', content: prompt }];
const args = [
  '--model', gguf, '--alias', model, '--host', '127.0.0.1', '--port', String(port),
  '--ctx-size', '4096', '--parallel', '1', '--n-gpu-layers', gpuLayers,
];
if (device) args.push('--device', device);

const service = spawn(process.env.LLAMA_SERVER_EXE, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
let log = '';
for (const output of [service.stdout, service.stderr]) {
  output.setEncoding('utf8').on('data', data => { log = (log + data).slice(-60_000); });
}
let exited = false;
const finished = new Promise(resolve => {
  service.once('error', error => { log += error.message; exited = true; });
  service.once('close', () => { exited = true; resolve(); });
});

try {
  let ready = false;
  const deadline = Date.now() + 120_000;
  while (!exited && Date.now() < deadline) {
    const response = await fetch(`${origin}/health`, { signal: AbortSignal.timeout(2_000) }).catch(() => null);
    if (response?.ok) { ready = true; break; }
    await sleep(500);
  }
  if (!ready) throw new Error(`llama.cpp did not start:\n${log.replaceAll(gguf, weights).slice(-5000)}`);
  const options = { temperature: 0.2, top_p: 0.9, max_tokens: 1800, seed: 20261113, stream: false };
  const requestAt = new Date().toISOString();
  const response = await fetch(`${origin}/v1/chat/completions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, messages, ...options }),
    signal: AbortSignal.timeout(210_000),
  });
  if (!response.ok) throw new Error(`llama.cpp returned HTTP ${response.status}`);
  const result = await response.json();
  assert.equal(result.model, model);
  const choice = result.choices[0];
  const answer = choice.message.content?.split('</think>').at(-1);
  if (!answer || answer.includes('<think>')) throw new Error('The model did not return a complete final answer.');
  await mkdir(directory, { recursive: true });
  await writeFile(join(directory, 'minimax-answer.md'), answer, 'utf8');
  const code = readModelCode(answer);
  await writeFile(join(directory, 'minimax-run.json'), `${JSON.stringify({
    provider: 'MiniMaxAI (public weights, local inference)', model, upstreamModelRevision,
    quantizationRepository: 'tensorblock/MiniMaxAI_SynLogic-7B-GGUF', weightsRevision, weights, weightsSha256,
    runtime: 'llama.cpp', build,
    command: ['llama-server', ...args.map(arg => arg === gguf ? weights : arg)],
    method: 'POST', path: '/v1/chat/completions', options, requestAt, finishedAt: new Date().toISOString(),
    responseId: result.id, finishReason: choice.finish_reason, usage: result.usage, timings: result.timings,
    promptSha256: sha256(prompt),
    answerSha256: sha256(answer), extractedCodeSha256: sha256(code),
  }, null, 2)}\n`, 'utf8');
  console.log(`Saved MiniMax answer and provenance to ${directory}`);
} finally {
  if (!exited) service.kill();
  await finished;
}
