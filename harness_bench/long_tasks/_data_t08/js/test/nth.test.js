'use strict';

const assert = require('node:assert/strict');
const { test } = require('./harness');
const {
  ChronoError,
  isLastWeekdayOfMonth,
  lastWeekdayOfMonth,
  nextWeekday,
  nthWeekdayOfMonth,
  prevWeekday,
  weekdayOccurrence,
  weekdaysInMonth,
} = require('../src');

test("n-th weekday of a month", () => {
  assert.deepEqual(nthWeekdayOfMonth(2024, 3, 'FR', -1), '2024-03-29');
  assert.deepEqual(nthWeekdayOfMonth(2024, 3, 'MO', 1), '2024-03-04');
  assert.deepEqual(nthWeekdayOfMonth(2024, 2, 'TH', 5), '2024-02-29');
  assert.deepEqual(nthWeekdayOfMonth(2024, 2, 'FR', 5), null);
  assert.deepEqual(nthWeekdayOfMonth(2024, 2, 'FR', -5), null);
  assert.throws(() => nthWeekdayOfMonth(2024, 2, 'FR', 0), { code: 'E_RANGE' });
  assert.throws(() => nthWeekdayOfMonth(2024, 2, 'FR', 6), { code: 'E_RANGE' });
  assert.throws(() => nthWeekdayOfMonth(2024, 2, 'fr', 1), { code: 'E_RANGE' });
  assert.deepEqual(lastWeekdayOfMonth(2024, 5, 'FR'), '2024-05-31');
});

test("occurrence from both ends", () => {
  assert.deepEqual(weekdayOccurrence('2024-03-29'), { n: 5, fromEnd: -1, weekday: 'FR' });
  assert.deepEqual(weekdayOccurrence('2024-03-22'), { n: 4, fromEnd: -2, weekday: 'FR' });
  assert.deepEqual(weekdayOccurrence('2024-02-01'), { n: 1, fromEnd: -5, weekday: 'TH' });
  assert.deepEqual(isLastWeekdayOfMonth('2024-03-25'), true);
  assert.deepEqual(isLastWeekdayOfMonth('2024-03-24'), false);
});

test("next and previous weekday", () => {
  assert.deepEqual(nextWeekday('2024-03-04', 'MO'), '2024-03-11');
  assert.deepEqual(nextWeekday('2024-03-04', 'MO', { inclusive: true }), '2024-03-04');
  assert.deepEqual(nextWeekday('2024-03-04', 'SU'), '2024-03-10');
  assert.deepEqual(prevWeekday('2024-03-04', 'MO'), '2024-02-26');
  assert.deepEqual(prevWeekday('2024-03-04', 'FR', { inclusive: true }), '2024-03-01');
  assert.deepEqual(weekdaysInMonth(2024, 2, 'TH'), ['2024-02-01', '2024-02-08', '2024-02-15', '2024-02-22', '2024-02-29']);
});
