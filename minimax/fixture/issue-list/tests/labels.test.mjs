import assert from 'node:assert/strict';
import test from 'node:test';
import { issues } from '../initial-data.mjs';
import { countLabels } from '../before/labels.mjs';

test('LAB-201 counts a label once per issue', () => {
  assert.equal(countLabels(issues).get('infra'), 3);
});
