'use strict';

const assert = require('node:assert/strict');
const { test } = require('./harness');
const {
  ChronoError,
  addDuration,
  ageOn,
  diffDates,
  durationDays,
  endOfMonthAfter,
  formatDuration,
  monthsBetween,
  negateDuration,
  parseDuration,
  subtractDuration,
} = require('../src');

test("parsing", () => {
  assert.deepEqual(parseDuration('P1Y2M'), { sign: 1, years: 1, months: 2, weeks: 0, days: 0 });
  assert.deepEqual(parseDuration(' -P3W '), { sign: -1, years: 0, months: 0, weeks: 3, days: 0 });
  assert.deepEqual(parseDuration('P1M2W'), { sign: 1, years: 0, months: 1, weeks: 2, days: 0 });
  assert.throws(() => parseDuration('P'), { code: 'E_PARSE' });
  assert.throws(() => parseDuration('PT1H'), { code: 'E_PARSE' });
  assert.throws(() => parseDuration('p1m'), { code: 'E_PARSE' });
  assert.throws(() => parseDuration('P100000D'), { code: 'E_RANGE' });
  assert.throws(() => parseDuration('P1D2M'), { code: 'E_PARSE' });
});

test("formatting", () => {
  assert.deepEqual(formatDuration({ months: 14 }), 'P14M');
  assert.deepEqual(formatDuration('P0Y0M1W'), 'P1W');
  assert.deepEqual(formatDuration({ sign: -1, days: 0 }), 'P0D');
  assert.deepEqual(formatDuration({ sign: -1, years: 1, days: 3 }), '-P1Y3D');
  assert.throws(() => formatDuration({ months: -1 }), { code: 'E_RANGE' });
  assert.throws(() => formatDuration({ sign: 2 }), { code: 'E_RANGE' });
  assert.throws(() => formatDuration({ month: 1 }), { code: 'E_TYPE' });
});

test("applying durations: months first with overflow, then days", () => {
  assert.deepEqual(addDuration('2024-01-31', 'P1M1D'), '2024-03-03');
  assert.deepEqual(addDuration('2024-03-31', '-P1M1D'), '2024-03-01');
  assert.deepEqual(addDuration('2023-01-31', 'P1M'), '2023-03-03');
  assert.deepEqual(addDuration('2024-02-29', 'P1Y'), '2025-03-01');
  assert.deepEqual(addDuration('2024-01-01', 'P2W3D'), '2024-01-18');
  assert.deepEqual(subtractDuration('2024-03-31', 'P1M'), '2024-03-02');
  assert.deepEqual(negateDuration('P1D'), { sign: -1, years: 0, months: 0, weeks: 0, days: 1 });
  assert.deepEqual(durationDays('P1M', '2024-02-01'), 29);
  assert.deepEqual(durationDays('P1M', '2024-01-31'), 31);
});

test("diffDates clamps", () => {
  assert.deepEqual(diffDates('2024-01-31', '2024-03-01'), { sign: 1, years: 0, months: 1, days: 1 });
  assert.deepEqual(diffDates('2024-03-01', '2024-01-31'), { sign: -1, years: 0, months: 1, days: 1 });
  assert.deepEqual(diffDates('2023-01-31', '2023-02-28'), { sign: 1, years: 0, months: 0, days: 28 });
  assert.deepEqual(diffDates('2020-02-29', '2024-02-28'), { sign: 1, years: 3, months: 11, days: 30 });
  assert.deepEqual(monthsBetween('2024-01-31', '2024-02-29'), 0);
  assert.deepEqual(monthsBetween('2024-05-15', '2023-01-20'), -15);
});

test("age and month ends", () => {
  assert.deepEqual(ageOn('2000-02-29', '2023-02-28'), 22);
  assert.deepEqual(ageOn('2000-02-29', '2023-03-01'), 23);
  assert.throws(() => ageOn('2000-02-29', '1999-01-01'), { code: 'E_RANGE' });
  assert.deepEqual(endOfMonthAfter('2024-01-31', 1), '2024-02-29');
  assert.deepEqual(endOfMonthAfter('2024-01-31', -2), '2023-11-30');
});
