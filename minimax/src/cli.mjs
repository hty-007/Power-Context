import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runChecks } from './checks.mjs';
import { fixtureSources, project, sha256, tasks } from './fixture.mjs';
import { powerContext } from './powercontext.mjs';
import { startPowerContext } from './powercontext-process.mjs';
import { importSources, retrieveSources, useSeekdb } from './seekdb.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const runtime = resolve(process.env.SDD_RUN_DIR || resolve(root, '.runtime/demo'));
const [command, value, variant, ...extra] = process.argv.slice(2);
const usage = [
  'pnpm sdd import <v1|v2>',
  'pnpm sdd context <assignee-filter|label-summary>',
  'pnpm sdd check assignee-filter <before|after>',
].join('\n');

async function withPowerContext(action) {
  let server;
  try {
    const external = process.env.SDD_PC_URL || process.env.SDD_PC_TOKEN;
    if (!external) server = await startPowerContext(runtime);
    const pc = server ? powerContext(server.url, server.token) : powerContext();
    const result = await action(pc);
    return {
      ...result, processId: process.pid, powerContext: {
        version: server?.version, processId: server?.pid, requests: pc.audit,
      },
    };
  } finally {
    await server?.stop();
  }
}

function summary(previous, revision, contextSha256, task) {
  if (!previous) return { status: 'empty', completed: [], pending: task.initialActions, verification: {}, nextAction: task.initialActions[0] };
  const state = previous.state;
  if (state.revision !== revision || state.contextSha256 !== contextSha256) return {
    status: 'specification-updated', previousRevision: state.revision, completed: [], pending: task.initialActions,
    verification: {}, earlierVerification: state.verification, nextAction: '按新规格重新运行基线测试',
  };
  return state;
}

function proof(checks) {
  return {
    implementationSha256: checks.implementationSha256, testHashes: checks.testHashes,
    results: checks.results.map(({ log, ...result }) => result),
  };
}

async function main() {
  if (!command || command === '--help') return console.log(usage);
  if (extra.length) throw new Error(usage);
  await mkdir(runtime, { recursive: true });
  if (command === 'import' && !variant) {
    const sources = await fixtureSources(value);
    const result = await useSeekdb(runtime, async (connection, version) => ({
      engine: version, revision: value, stored: await importSources(connection, value, sources),
      records: sources.map(({ id, path, sha256 }) => ({ id, path, sha256 })),
    }));
    return console.log(JSON.stringify(result, null, 2));
  }
  if (!Object.hasOwn(tasks, value)) throw new Error(usage);
  if (!(command === 'context' && !variant || command === 'check' && value === 'assignee-filter' && ['before', 'after'].includes(variant))) {
    throw new Error(usage);
  }

  const task = tasks[value];
  const db = await useSeekdb(runtime, async (connection, version) => ({
    engine: version, ...await retrieveSources(connection, value),
  }));
  const contextSha256 = sha256(JSON.stringify(db.sources.map(({ id, path, sha256: hash }) => [id, path, hash])));
  const result = await withPowerContext(async pc => {
    const memory = await pc.openTask(value);
    const previous = await memory.load();
    const progress = summary(previous, db.revision, contextSha256, task);
    const base = { project, taskId: value, task, scopeId: memory.scope, revision: db.revision, contextSha256 };

    if (command === 'context') return {
      ...base, seekdb: { engine: db.engine, ...db.query }, sources: db.sources,
      artifact: previous?.artifact ?? null, progress,
    };
    if (variant === 'after' && !progress.verification.before) {
      throw new Error('Run the before checks for the current specification before running after.');
    }
    if (variant === 'before' && progress.verification.after) {
      throw new Error('The current specification is already verified. Use a fresh SDD_RUN_DIR to replay.');
    }
    const checks = await runChecks(variant);
    const verification = { ...progress.verification, [variant]: proof(checks) };
    const before = verification.before;
    if (variant === 'after' && JSON.stringify(before.testHashes) !== JSON.stringify(checks.testHashes)) {
      throw new Error('The tests or fixture data changed since the baseline.');
    }
    const completed = variant === 'before' ? task.initialActions.slice(0, 1) : task.initialActions;
    const pending = task.initialActions.slice(completed.length);
    const next = {
      status: pending.length ? 'in-progress' : 'complete',
      revision: db.revision, contextSha256, completed, pending, verification,
      nextAction: pending[0] ?? '提交已验证的修改',
    };
    const artifact = await memory.save(next, previous);
    return { ...base, artifact, progress: next, checks };
  });
  console.log(JSON.stringify(result, null, 2));
}

try {
  await main();
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
