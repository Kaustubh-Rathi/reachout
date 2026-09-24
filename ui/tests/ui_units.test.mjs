import test from 'node:test';
import assert from 'node:assert/strict';

import { formatDate, formatTime } from '../modules/format.js';
import { getAction, registerActions } from '../modules/actions.js';
import { state } from '../modules/store.js';
import { allLoadsSucceeded } from '../modules/load_state.js';

test('formatDate returns empty values and formats valid dates', () => {
  assert.equal(formatDate(), '');
  assert.equal(formatDate('not-a-date'), '');
  assert.match(formatDate('2026-08-17T12:34:00Z'), /17 Aug 2026 \d{2}:\d{2}/);
});

test('formatTime normalizes timezone-less ISO values and rejects invalid values', () => {
  assert.equal(formatTime(), '');
  assert.equal(formatTime(''), '');
  assert.equal(formatTime('invalid'), '');
  assert.match(formatTime('2026-08-17T12:34:00'), /^\d{2}:\d{2}$/);
  assert.match(formatTime('2026-08-17T12:34:00Z'), /^\d{2}:\d{2}$/);
});

test('action registry validates registrations and resolves actions', () => {
  const action = () => 'ok';
  registerActions({ __coverage_action: action });
  assert.equal(getAction('__coverage_action'), action);
  assert.throws(() => registerActions({ __coverage_action: action }), /Duplicate action registration/);
  assert.throws(() => registerActions({ __coverage_invalid: null }), /is not a function/);
  assert.throws(() => getAction('missing'), /Unknown action/);
});

test('shared UI state starts with safe filter defaults', () => {
  assert.equal(state.selectedCompany, 'ALL');
  assert.equal(state.selectedCrmStatus, 'ALL');
  assert.equal(state.selectedCoverageStatus, 'ALL');
  assert.equal(state.selectedPriorityFilter, 'ALL');
  assert.equal(state.contactsPage, 1);
});

test('initial load readiness requires every explicit success', () => {
  assert.equal(allLoadsSucceeded([
    { status: 'fulfilled', value: true },
    { status: 'fulfilled', value: true },
  ]), true);
  assert.equal(allLoadsSucceeded([{ status: 'fulfilled', value: true }, { status: 'fulfilled', value: false }]), false);
  assert.equal(allLoadsSucceeded([{ status: 'fulfilled', value: true }, { status: 'rejected', reason: new Error('failed') }]), false);
  assert.equal(allLoadsSucceeded([{ status: 'fulfilled', value: undefined }]), false);
  assert.equal(allLoadsSucceeded([]), false);
});
