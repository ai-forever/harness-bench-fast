'use strict';

const assert = require('node:assert/strict');
const { test } = require('./harness');
const {
  ChronoError,
  daysInPeriod,
  fiscalYear,
  halfOf,
  periodContains,
  periodKind,
  periodOf,
  periodRange,
  periodsBetween,
  quarterOf,
  shiftPeriod,
} = require('../src');

test("quarters, halves and fiscal years", () => {
  assert.deepEqual(quarterOf('2024-08-17'), 3);
  assert.deepEqual(halfOf('2024-06-30'), 1);
  assert.deepEqual(fiscalYear('2024-06-30', 7), 2024);
  assert.deepEqual(fiscalYear('2024-07-01', 7), 2025);
  assert.deepEqual(fiscalYear('2024-07-01', 1), 2024);
  assert.throws(() => fiscalYear('2024-07-01', 13), { code: 'E_RANGE' });
});

test("labels of dates", () => {
  assert.deepEqual(periodOf('2024-08-15', 'quarter'), '2024-Q3');
  assert.deepEqual(periodOf('2024-08-15', 'half'), '2024-H2');
  assert.deepEqual(periodOf('2024-08-15', 'month'), '2024-08');
  assert.deepEqual(periodOf('2021-01-03', 'week'), '2020-W53');
  assert.deepEqual(periodOf('2024-08-15', 'fiscal', { fiscalStart: 4 }), 'FY2025');
  assert.deepEqual(periodOf('2024-08-15', 'fiscal'), 'FY2024');
  assert.throws(() => periodOf('2024-08-15', 'day'), { code: 'E_RANGE' });
});

test("ranges", () => {
  assert.deepEqual(periodRange('2024-Q1'), ['2024-01-01', '2024-03-31']);
  assert.deepEqual(periodRange('2024-H2'), ['2024-07-01', '2024-12-31']);
  assert.deepEqual(periodRange('2024-02'), ['2024-02-01', '2024-02-29']);
  assert.deepEqual(periodRange('2025-W01'), ['2024-12-30', '2025-01-05']);
  assert.deepEqual(periodRange('FY2025', { fiscalStart: 7 }), ['2024-07-01', '2025-06-30']);
  assert.deepEqual(periodRange('FY2025'), ['2025-01-01', '2025-12-31']);
  assert.throws(() => periodRange('2024-Q5'), { code: 'E_RANGE' });
  assert.throws(() => periodRange('2021-W53'), { code: 'E_RANGE' });
  assert.throws(() => periodRange('2024-q1'), { code: 'E_PARSE' });
  assert.throws(() => periodRange('2024', { fiscalStart: 0 }), { code: 'E_RANGE' });
});

test("shifting and listing", () => {
  assert.deepEqual(shiftPeriod('2024-Q4', 1), '2025-Q1');
  assert.deepEqual(shiftPeriod('2024-01', -13), '2022-12');
  assert.deepEqual(shiftPeriod('2020-W53', 1), '2021-W01');
  assert.deepEqual(shiftPeriod('FY2025', -2, { fiscalStart: 7 }), 'FY2023');
  assert.deepEqual(shiftPeriod('2024-H1', 3), '2025-H2');
  assert.deepEqual(periodsBetween('2024-11-20', '2025-02-03', 'month'), ['2024-11', '2024-12', '2025-01', '2025-02']);
  assert.deepEqual(periodsBetween('2024-12-25', '2025-01-10', 'week'), ['2024-W52', '2025-W01', '2025-W02']);
  assert.deepEqual(periodsBetween('2024-06-01', '2025-08-01', 'fiscal', { fiscalStart: 7 }), ['FY2024', 'FY2025', 'FY2026']);
  assert.deepEqual(periodsBetween('2025-01-01', '2024-01-01', 'month'), []);
});

test("containment and length", () => {
  assert.deepEqual(periodContains('FY2025', '2024-07-01', { fiscalStart: 7 }), true);
  assert.deepEqual(periodContains('2025-W01', '2024-12-31'), true);
  assert.deepEqual(daysInPeriod('2024-H1'), 182);
  assert.deepEqual(daysInPeriod('2023-Q1'), 90);
  assert.deepEqual(periodKind('FY2024'), 'fiscal');
});
