import assert from 'node:assert/strict';
import test from 'node:test';
import { issues } from '../initial-data.mjs';
import { listIssues } from './load-implementation.mjs';

const ids = rows => rows.map(row => row.id);

test('ISS-101 normalizes both owner names, matches the whole name and preserves order', () => {
  assert.deepEqual(ids(listIssues(issues, { assignee: ' lIn ' })), ['T-08', 'T-03', 'T-07']);
});

test('ISS-101 trims tabs and full-width spaces on both sides', () => {
  assert.deepEqual(ids(listIssues(issues, { assignee: '\tLIN\u3000' })), ['T-08', 'T-03', 'T-07']);
});

test('ISS-101 returns an empty list when no issue has that owner', () => {
  assert.deepEqual(ids(listIssues(issues, { assignee: 'Unknown' })), []);
});

test('ISS-103 combines owner, status and title by intersection', () => {
  const filters = Object.freeze({ assignee: 'LIN', status: 'open', query: ' cAcHe ' });
  const result = listIssues(issues, filters);
  assert.deepEqual(ids(result), ['T-08']);
  assert.equal(result[0], issues[0]);
  assert.notEqual(result, issues);
});
