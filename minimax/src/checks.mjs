import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fixture, sha256 } from './fixture.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const tests = [
  { kind: 'target', file: 'assignee.test.mjs', count: 4 },
  { kind: 'regression', file: 'regression.test.mjs', count: 3 },
];
const testInputs = ['tests/load-implementation.mjs', 'initial-data.mjs', ...tests.map(test => `tests/${test.file}`)];
const content = async path => (await readFile(join(fixture, path), 'utf8')).replace(/\r\n/g, '\n');

export async function runChecks(variant) {
  if (!['before', 'after'].includes(variant)) throw new Error('Specify before or after.');
  const implementation = `${variant}/list.mjs`;
  const testHashes = Object.fromEntries(await Promise.all(testInputs.map(async path => [path, sha256(await content(path))])));
  const results = tests.map(({ kind, file, count }) => {
    const args = ['--test', '--test-reporter=tap', `fixture/issue-list/tests/${file}`];
    const result = spawnSync(process.execPath, args, {
      cwd: root, env: { ...process.env, SDD_IMPLEMENTATION: variant }, encoding: 'utf8', timeout: 30_000, windowsHide: true,
    });
    if (result.error) throw result.error;
    const log = result.stdout.replaceAll(root.replaceAll('\\', '/'), './').replaceAll(root.replaceAll('\\', '\\\\'), './');
    const tally = key => Number(log.match(new RegExp(`^# ${key} (\\d+)\r?$`, 'm'))?.[1]);
    const expectedFailure = kind === 'target' && variant === 'before';
    const passed = tally('pass');
    const failed = tally('fail');
    const assertionFailures = [...log.matchAll(/code: 'ERR_ASSERTION'/g)].length;
    assert.equal(tally('tests'), count, log + result.stderr);
    assert.equal(tally('skipped'), 0, log);
    assert.equal(result.status, expectedFailure ? 1 : 0, log);
    assert.equal(passed, expectedFailure ? 0 : count, log);
    assert.equal(failed, expectedFailure ? count : 0, log);
    assert.equal(assertionFailures, failed, log);
    return { kind, command: `node ${args.join(' ')}`, exitCode: result.status, passed, failed, assertionFailures, log };
  });
  return { variant, implementation, implementationSha256: sha256(await content(implementation)), testHashes, results };
}
