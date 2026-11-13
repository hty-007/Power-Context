import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import os from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fixture, fixtureSources, sha256 } from '../src/fixture.mjs';
import { assembleImplementation, developmentPrompt, formatRepairPrompt, readModelCode } from '../src/prompt.mjs';

const projectRoot = fileURLToPath(new URL('../', import.meta.url));
const logDirectory = resolve(process.argv[2] || join(projectRoot, '.runtime/verification'));
const runtimeRoot = join(projectRoot, '.runtime');
await mkdir(runtimeRoot, { recursive: true });
const runtime = await mkdtemp(join(runtimeRoot, 'verify-'));
await mkdir(logDirectory, { recursive: true });
const logs = [];

async function cli(label, ...args) {
  const command = ['node', 'src/cli.mjs', ...args];
  const child = spawn(process.execPath, command.slice(1), {
    cwd: projectRoot, env: { ...process.env, SDD_RUN_DIR: runtime },
    windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], timeout: 150_000,
  });
  let stdout = '';
  let stderr = '';
  child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk; });
  child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk; });
  const exitCode = await new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', resolve);
  });
  assert.equal(exitCode, 0, `${command.join(' ')}\n${stderr}\n${stdout}`);
  const filename = `${label}.json`;
  const result = JSON.parse(stdout);
  await writeFile(join(logDirectory, filename), stdout.replace(/\r\n/g, '\n'), 'utf8');
  logs.push({ command, exitCode, output: filename });
  console.log(`${label} · exit 0`);
  return result;
}

async function verifySources(context, revision) {
  assert.equal(context.revision, revision);
  assert.match(context.seekdb.engine, /OceanBase seekdb-v1\.4\.0/);
  assert.equal(context.seekdb.rows, context.sources.length);
  const expected = (await fixtureSources(revision)).filter(source => source.tasks.includes(context.taskId));
  assert.deepEqual(context.sources.map(s => s.id).sort(), expected.map(s => s.id).sort());
  for (const source of context.sources) {
    const { tasks, ...expectedSource } = expected.find(entry => entry.id === source.id);
    assert.deepEqual(source, expectedSource);
  }
}

function verifyResume(saved, resumed) {
  assert.notEqual(saved.processId, resumed.processId);
  if (saved.powerContext.processId && resumed.powerContext.processId) {
    assert.notEqual(saved.powerContext.processId, resumed.powerContext.processId);
  }
  assert.equal(resumed.scopeId, saved.scopeId);
  assert.equal(resumed.artifact.artifact_id, saved.artifact.artifact_id);
  assert.equal(resumed.artifact.revision, saved.artifact.revision);
  assert.deepEqual(resumed.progress, saved.progress);
  assert.ok(resumed.powerContext.requests.some(r => r.path === '/v1/atomic-memory/list' && r.status === 200));
}

const evidence = name => readFile(join(projectRoot, 'evidence', name), 'utf8').then(text => text.replace(/\r\n/g, '\n'));
const answer = await evidence('minimax-answer.md');
const prompt = await evidence('minimax-prompt.txt');
const modelRecord = JSON.parse(await evidence('minimax-run.json'));
const firstAnswer = await evidence('first-attempt/minimax-answer.md');
const firstRecord = JSON.parse(await evidence('first-attempt/minimax-run.json'));
const instructions = await evidence('repair-instructions.txt');
const repairPrompt = await evidence('repair-chatml.txt');
const development = JSON.parse(await evidence('development-context.json'));
const afterFile = (await readFile(join(fixture, 'after/list.mjs'), 'utf8')).replace(/\r\n/g, '\n');
assert.equal(developmentPrompt(development), prompt);
assert.equal(sha256(prompt), firstRecord.promptSha256);
assert.equal(sha256(firstAnswer), firstRecord.answerSha256);
assert.equal(sha256(readModelCode(firstAnswer)), firstRecord.extractedCodeSha256);
assert.ok(instructions.includes(firstAnswer));
assert.equal(formatRepairPrompt(instructions), repairPrompt);
assert.equal(sha256(instructions), modelRecord.instructionsSha256);
assert.equal(sha256(repairPrompt), modelRecord.promptSha256);
assert.equal(sha256(answer), modelRecord.answerSha256);
assert.equal(sha256(afterFile), modelRecord.extractedCodeSha256);
assert.equal(afterFile, assembleImplementation(firstAnswer, answer));
await verifySources(development, 'v1');

const imported = await cli('01-import-v1', 'import', 'v1');
assert.equal(imported.stored, 11);
const initial = await cli('02-initial-context', 'context', 'assignee-filter');
await verifySources(initial, 'v1');
for (const id of ['ISS-101', 'ISS-102', 'ISS-103', 'CODE-LIST', 'TEST-ASSIGNEE', 'TEST-REGRESSION']) {
  assert.ok(initial.sources.some(source => source.id === id), `Missing required source ${id}`);
}
assert.equal(initial.artifact, null);
assert.equal(initial.progress.status, 'empty');
const before = await cli('03-before-checks', 'check', 'assignee-filter', 'before');
assert.equal(before.progress.status, 'in-progress');
assert.ok(before.powerContext.requests.some(r => r.method === 'POST' && r.path.endsWith('/artifacts') && r.status === 201));
const resumed = await cli('04-resumed-context', 'context', 'assignee-filter');
verifyResume(before, resumed);
assert.equal(resumed.progress.nextAction, '让 MiniMax 实现负责人筛选');
assert.equal(developmentPrompt(resumed), prompt);

const updated = await cli('05-import-v2', 'import', 'v2');
assert.equal(updated.stored, imported.stored);
const updatedContext = await cli('06-updated-context', 'context', 'assignee-filter');
await verifySources(updatedContext, 'v2');
const spec = context => context.sources.find(source => source.id === 'ISS-101');
assert.notEqual(spec(updatedContext).sha256, spec(initial).sha256);
assert.notEqual(updatedContext.contextSha256, initial.contextSha256);
assert.match(spec(updatedContext).content, /全角空格 U\+3000/);
assert.equal(updatedContext.progress.status, 'specification-updated');
assert.equal(updatedContext.progress.nextAction, '按新规格重新运行基线测试');
assert.deepEqual(updatedContext.progress.earlierVerification.before, before.progress.verification.before);

const secondBefore = await cli('07-before-v2', 'check', 'assignee-filter', 'before');
assert.ok(secondBefore.artifact.revision > before.artifact.revision);
const after = await cli('08-after-v2', 'check', 'assignee-filter', 'after');
assert.equal(after.progress.status, 'complete');
assert.deepEqual(after.checks.testHashes, secondBefore.checks.testHashes);
assert.equal(after.checks.implementationSha256, modelRecord.extractedCodeSha256);
assert.equal(after.artifact.artifact_id, secondBefore.artifact.artifact_id);
assert.ok(after.artifact.revision > secondBefore.artifact.revision);
assert.ok(after.powerContext.requests.some(r => r.method === 'PUT' && r.status === 200));
const final = await cli('09-completed-context', 'context', 'assignee-filter');
verifyResume(after, final);
assert.deepEqual(final.progress.pending, []);
const unrelated = await cli('10-label-context', 'context', 'label-summary');
await verifySources(unrelated, 'v2');
assert.equal(unrelated.progress.status, 'empty');
assert.ok(!unrelated.sources.some(s => s.id === 'CODE-LIST'));

await writeFile(join(logDirectory, 'summary.json'), `${JSON.stringify({
  verifiedAt: new Date().toISOString(), node: process.version,
  system: { platform: os.platform(), version: os.release(), architecture: os.arch() },
  engine: initial.seekdb.engine, powerContextVersion: initial.powerContext.version,
  importedRows: imported.stored, selectedRows: initial.seekdb.rows, specHashes: [spec(initial).sha256, spec(updatedContext).sha256],
  model: modelRecord.model, modelCodeSha256: modelRecord.extractedCodeSha256,
  baseline: before.progress.verification.before, final: final.progress.verification.after,
  stateRevisions: [before.artifact.revision, secondBefore.artifact.revision, after.artifact.revision],
  initialProcess: initial.processId, persistedProcess: after.processId, resumedProcess: final.processId,
  commands: logs,
}, null, 2)}\n`, 'utf8');
console.log('Verified initialization, model output, context selection, database use, state restart, and unchanged tests.');
