'use strict';

const assert = require('node:assert/strict');
const { test } = require('./harness');
const {
  ChronoError,
  addBusinessDays,
  businessDaysBetween,
  businessDaysInMonth,
  businessDaysList,
  lastBusinessDay,
  nextBusinessDay,
  nthBusinessDay,
  prevBusinessDay,
  workHoursBetween,
  workHoursInMonth,
} = require('../src');

test("addBusinessDays never counts the start", () => {
  assert.deepEqual(addBusinessDays('2024-03-08', 1), '2024-03-11');
  assert.deepEqual(addBusinessDays('2024-03-09', 0), '2024-03-11');
  assert.deepEqual(addBusinessDays('2024-03-09', 1), '2024-03-11');
  assert.deepEqual(addBusinessDays('2024-03-09', -1), '2024-03-08');
  assert.deepEqual(addBusinessDays('2024-03-11', 0), '2024-03-11');
  assert.deepEqual(addBusinessDays('2024-05-08', 1, { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), '2024-05-13');
  assert.deepEqual(addBusinessDays('2024-04-26', 1, { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), '2024-04-27');
  assert.deepEqual(addBusinessDays('2024-05-13', -2, { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), '2024-05-07');
});

test("businessDaysBetween counts (start, end]", () => {
  assert.deepEqual(businessDaysBetween('2024-03-08', '2024-03-11'), 1);
  assert.deepEqual(businessDaysBetween('2024-03-11', '2024-03-08'), -1);
  assert.deepEqual(businessDaysBetween('2024-03-09', '2024-03-10'), 0);
  assert.deepEqual(businessDaysBetween('2024-03-04', '2024-03-04'), 0);
  assert.deepEqual(businessDaysBetween('2024-03-04', '2024-03-08'), 4);
  assert.deepEqual(businessDaysBetween('2024-04-30', '2024-05-13', { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), 6);
});

test("workHoursBetween is inclusive on both ends", () => {
  assert.deepEqual(workHoursBetween('2024-05-06', '2024-05-10', { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), 23);
  assert.deepEqual(workHoursBetween('2024-03-04', '2024-03-04'), 8);
  assert.deepEqual(workHoursBetween('2024-03-05', '2024-03-04'), 0);
});

test("monthly figures", () => {
  assert.deepEqual(nthBusinessDay(2024, 5, 1, { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), '2024-05-02');
  assert.deepEqual(nthBusinessDay(2024, 5, -1, { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), '2024-05-31');
  assert.deepEqual(nthBusinessDay(2024, 5, 25), null);
  assert.throws(() => nthBusinessDay(2024, 5, 0), { code: 'E_RANGE' });
  assert.deepEqual(lastBusinessDay(2024, 4, { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), '2024-04-30');
  assert.deepEqual(businessDaysInMonth(2024, 5, { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), 20);
  assert.deepEqual(workHoursInMonth(2024, 5, { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), 159);
  assert.deepEqual(workHoursInMonth(2024, 2), 168);
});

test("neighbours and lists", () => {
  assert.deepEqual(nextBusinessDay('2024-03-08'), '2024-03-11');
  assert.deepEqual(prevBusinessDay('2024-03-11'), '2024-03-08');
  assert.deepEqual(businessDaysList('2024-05-06', '2024-05-14', { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), ['2024-05-06', '2024-05-07', '2024-05-08', '2024-05-13', '2024-05-14']);
  assert.throws(() => businessDaysList('2000-01-01', '2020-01-01'), { code: 'E_LIMIT' });
});
