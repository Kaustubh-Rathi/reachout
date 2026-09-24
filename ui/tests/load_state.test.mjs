import test from 'node:test';
import assert from 'node:assert/strict';

import { allLoadsSucceeded } from '../modules/load_state.js';

test('readiness is true only when every load explicitly succeeds', () => {
  assert.equal(allLoadsSucceeded([{ status: 'fulfilled', value: true }]), true);
  assert.equal(allLoadsSucceeded([{ status: 'fulfilled', value: false }]), false);
  assert.equal(allLoadsSucceeded([{ status: 'rejected', reason: new Error('failed') }]), false);
  assert.equal(allLoadsSucceeded([{ status: 'fulfilled' }]), false);
  assert.equal(allLoadsSucceeded([]), false);
});
