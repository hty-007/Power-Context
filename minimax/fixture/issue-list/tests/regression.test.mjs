import assert from 'node:assert/strict';
import test from 'node:test';
import { issues } from '../initial-data.mjs';
import { listIssues } from './load-implementation.mjs';

const ids = rows => rows.map(row => row.id);

test('ISS-102 omitted and blank owners keep every issue, including unassigned issues', () => {
  for (const filters of [{}, { assignee: null }, { assignee: '' }, { assignee: '\t\u3000 ' }]) {
    assert.deepEqual(listIssues(issues, filters), issues);
  }
  const result = listIssues(issues);
  assert.deepEqual(result, issues);
  assert.notEqual(result, issues);
  assert.equal(result[0], issues[0]);
});

test('ISS-103 status is exact and title search is case insensitive', () => {
  assert.deepEqual(ids(listIssues(issues, { status: 'open', query: ' CACHE ' })), ['T-08', 'T-11', 'T-01']);
  assert.deepEqual(listIssues(issues, { status: 'Open' }), []);
});

test('ISS-103 title-only search and empty result behavior stay compatible', () => {
  assert.deepEqual(ids(listIssues(issues, { query: 'efresh' })), ['T-03']);
  assert.deepEqual(listIssues(issues, { query: 'not present' }), []);
  assert.deepEqual(listIssues([], { status: 'open' }), []);
});
