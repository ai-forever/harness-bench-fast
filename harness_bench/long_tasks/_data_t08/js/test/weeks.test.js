'use strict';

const assert = require('node:assert/strict');
const { test } = require('./harness');
const {
  ChronoError,
  isoWeek,
  isoWeekLabel,
  isoWeekStart,
  isoWeeksInYear,
  weekOfMonth,
  weekOfYear,
  weeksInMonth,
} = require('../src');

test("ISO week-year differs from the calendar year around New Year", () => {
  assert.deepEqual(isoWeek('2021-01-03'), { year: 2020, week: 53 });
  assert.deepEqual(isoWeek('2024-12-30'), { year: 2025, week: 1 });
  assert.deepEqual(isoWeek('2024-03-05'), { year: 2024, week: 10 });
  assert.deepEqual(isoWeekLabel('2021-01-03'), '2020-W53');
  assert.deepEqual(isoWeekLabel('2026-01-01'), '2026-W01');
});

test("ISO week starts and lengths", () => {
  assert.deepEqual(isoWeekStart(2020, 53), '2020-12-28');
  assert.deepEqual(isoWeekStart(2025, 1), '2024-12-30');
  assert.throws(() => isoWeekStart(2021, 53), { code: 'E_RANGE' });
  assert.deepEqual(isoWeeksInYear(2020), 53);
  assert.deepEqual(isoWeeksInYear(2026), 53);
  assert.deepEqual(isoWeeksInYear(2021), 52);
});

test("wall-calendar rows", () => {
  assert.deepEqual(weekOfMonth('2024-09-01'), 1);
  assert.deepEqual(weekOfMonth('2024-09-02'), 2);
  assert.deepEqual(weekOfMonth('2024-09-02', { weekStart: 'SU' }), 1);
  assert.deepEqual(weekOfMonth('2024-09-30'), 6);
  assert.deepEqual(weekOfYear('2024-01-07'), 1);
  assert.deepEqual(weekOfYear('2024-12-31'), 53);
  assert.deepEqual(weeksInMonth(2021, 2), 4);
  assert.deepEqual(weeksInMonth(2024, 9), 6);
  assert.deepEqual(weeksInMonth(2024, 9, { weekStart: 'SU' }), 5);
});
