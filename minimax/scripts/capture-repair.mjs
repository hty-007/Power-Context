import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { sha256 } from '../src/fixture.mjs';
import { assembleImplementation, formatRepairPrompt, readModelCode } from '../src/prompt.mjs';

const [input, formatted, output, runtimeLog, timing] = process.argv.slice(2);
if (!input || !formatted || !output || !runtimeLog || !timing) {
  throw new Error('Usage: node scripts/capture-repair.mjs <instructions> <chatml> <stdout> <stderr> <timing.json>');
}
const [instructions, prompt, rawOutput, log, time, first] = await Promise.all([
  readFile(input, 'utf8'), readFile(formatted, 'utf8'), readFile(output, 'utf8'), readFile(runtimeLog, 'utf8'),
  readFile(timing, 'utf8').then(JSON.parse), readFile('evidence/first-attempt/minimax-answer.md', 'utf8'),
]);
assert.equal(prompt, formatRepairPrompt(instructions));
const answer = rawOutput.replace(/\r\n/g, '\n').split('</think>').at(-1)
  .replace(/\s*\[end of text\]\s*$/, '').trim();
assert.ok(!answer.includes('<think>'), 'No complete final response.');
const predicate = readModelCode(answer).trim();
const implementation = assembleImplementation(first, answer);
const performance = log.split(/\r?\n/).filter(line => line.includes('common_perf_print'))
  .map(line => line.split('common_perf_print:').at(-1).trim());
const generation = {
  provider: 'MiniMaxAI (public weights, local inference)', model: 'MiniMaxAI/SynLogic-7B',
  upstreamModelRevision: 'bfcdfe2571549f8c40f1169cd62221abed5a5cbe',
  quantizationRepository: 'tensorblock/MiniMaxAI_SynLogic-7B-GGUF', weightsRevision: 'a05e87469d413a5a433358e90880e102184ba5a3',
  weights: 'SynLogic-7B-Q3_K_M.gguf', weightsSha256: '21be91c718f3909a7faa4a4705e432bc477bfec9a687a698ff5e2b5f9dab8e72',
  runtime: 'llama.cpp', build: '1.0.0-dev (build 12360, commit 2d8a32502)',
  command: [
    'llama-completion', '--model', 'SynLogic-7B-Q3_K_M.gguf', '--device', 'Vulkan0', '--n-gpu-layers', '99',
    '--ctx-size', '4096', '--no-conversation', '--no-display-prompt', '--simple-io', '--no-warmup',
    '--color', 'off', '--file', 'repair-chatml.txt', '--n-predict', '160', '--temp', '0.15', '--top-p', '0.9', '--seed', '314159',
  ],
  startedAt: time.started, finishedAt: time.finished, exitCode: 0, performance,
  instructionsSha256: sha256(instructions), promptSha256: sha256(prompt), answerSha256: sha256(answer),
  initialCodeSha256: sha256(readModelCode(first)), replacementPredicateSha256: sha256(predicate),
  extractedCodeSha256: sha256(implementation),
};
await mkdir('fixture/issue-list/after', { recursive: true });
await writeFile('fixture/issue-list/after/list.mjs', implementation, 'utf8');
await writeFile(join('evidence', 'repair-instructions.txt'), instructions, 'utf8');
await writeFile(join('evidence', 'repair-chatml.txt'), prompt, 'utf8');
await writeFile(join('evidence', 'minimax-answer.md'), answer, 'utf8');
await writeFile(join('evidence', 'minimax-run.json'), `${JSON.stringify(generation, null, 2)}\n`, 'utf8');
