'use strict';

const assert = require('node:assert/strict');
const { test } = require('./harness');
const {
  ChronoError,
  addDays,
  addMonths,
  addMonthsClamped,
  addYears,
  clampDate,
  compareDates,
  dateParts,
  dayOfYear,
  daysInMonth,
  daysInYear,
  diffDays,
  eachDay,
  endOfMonth,
  endOfQuarter,
  endOfWeek,
  endOfYear,
  isLeapYear,
  isSameMonth,
  isValidDate,
  isWeekend,
  makeDate,
  maxDate,
  minDate,
  sortDates,
  startOfMonth,
  startOfQuarter,
  startOfWeek,
  startOfYear,
  toCompact,
  weekday,
  weekdayCode,
} = require('../src');

test("leap years follow the Gregorian rule", () => {
  assert.deepEqual(isLeapYear(2024), true);
  assert.deepEqual(isLeapYear(2023), false);
  assert.deepEqual(isLeapYear(1900), false);
  assert.deepEqual(isLeapYear(2000), true);
  assert.deepEqual(isLeapYear(2100), false);
});

test("days in month and year", () => {
  assert.deepEqual(daysInMonth(2024, 2), 29);
  assert.deepEqual(daysInMonth(2023, 2), 28);
  assert.deepEqual(daysInMonth(2024, 4), 30);
  assert.throws(() => daysInMonth(2024, 13), { code: 'E_RANGE' });
  assert.deepEqual(daysInYear(2024), 366);
  assert.deepEqual(daysInYear(2025), 365);
});

test("strict ISO validation", () => {
  assert.deepEqual(isValidDate('2024-02-29'), true);
  assert.deepEqual(isValidDate('2023-02-29'), false);
  assert.deepEqual(isValidDate('2024-2-29'), false);
  assert.deepEqual(isValidDate(' 2024-02-29'), false);
  assert.deepEqual(isValidDate(20240229), false);
  assert.deepEqual(isValidDate('0999-12-31'), false);
});

// shape errors are E_PARSE, impossible dates E_RANGE, wrong types E_TYPE
test("errors on malformed dates", () => {
  assert.throws(() => addDays('2024-5-1', 1), { code: 'E_PARSE' });
  assert.throws(() => addDays('2024-04-31', 1), { code: 'E_RANGE' });
  assert.throws(() => addDays(null, 1), { code: 'E_TYPE' });
  assert.throws(() => addDays('2024-05-01', 1.5), { code: 'E_TYPE' });
});

test("makeDate validates in the order year, month, day", () => {
  assert.deepEqual(makeDate(2024, 2, 29), '2024-02-29');
  assert.throws(() => makeDate(2023, 2, 29), { code: 'E_RANGE' });
  assert.throws(() => makeDate(999, 13, 40), { code: 'E_RANGE' });
  assert.throws(() => makeDate(2024, 0, 1), { code: 'E_RANGE' });
});

test("weekday numbering starts on Monday", () => {
  assert.deepEqual(weekday('2024-03-04'), 0);
  assert.deepEqual(weekday('2024-03-10'), 6);
  assert.deepEqual(weekdayCode('2024-03-10'), 'SU');
  assert.deepEqual(weekday('1970-01-01'), 3);
  assert.deepEqual(dayOfYear('2024-12-31'), 366);
  assert.deepEqual(dayOfYear('2023-03-01'), 60);
});

// the overflow is part of the contract: stored schedules depend on it
test("addMonths overflows like Date#setMonth", () => {
  assert.deepEqual(addMonths('2024-01-31', 1), '2024-03-02');
  assert.deepEqual(addMonths('2023-01-31', 1), '2023-03-03');
  assert.deepEqual(addMonths('2024-03-31', -1), '2024-03-02');
  assert.deepEqual(addMonths('2024-05-31', 1), '2024-07-01');
  assert.deepEqual(addMonths('2024-01-29', 1), '2024-02-29');
  assert.deepEqual(addMonths('2024-12-31', -1), '2024-12-01');
  assert.deepEqual(addMonths('2024-10-31', 4), '2025-03-03');
  assert.deepEqual(addMonths('2024-08-31', -6), '2024-03-02');
});

test("addMonthsClamped sticks to the month end", () => {
  assert.deepEqual(addMonthsClamped('2024-01-31', 1), '2024-02-29');
  assert.deepEqual(addMonthsClamped('2023-01-31', 1), '2023-02-28');
  assert.deepEqual(addMonthsClamped('2024-03-31', -1), '2024-02-29');
  assert.deepEqual(addMonthsClamped('2024-05-31', 1), '2024-06-30');
});

test("addYears inherits the overflow", () => {
  assert.deepEqual(addYears('2024-02-29', 1), '2025-03-01');
  assert.deepEqual(addYears('2024-02-29', 4), '2028-02-29');
  assert.deepEqual(addYears('2024-02-29', -1), '2023-03-01');
  assert.throws(() => addYears('9999-06-01', 1), { code: 'E_RANGE' });
});

test("diff and compare", () => {
  assert.deepEqual(diffDays('2024-03-01', '2024-02-28'), -2);
  assert.deepEqual(diffDays('2023-12-31', '2025-01-01'), 367);
  assert.deepEqual(compareDates('2024-01-01', '2024-01-02'), -1);
  assert.deepEqual(compareDates('2024-01-02', '2024-01-02'), 0);
});

test("period boundaries", () => {
  assert.deepEqual(startOfMonth('2024-02-17'), '2024-02-01');
  assert.deepEqual(endOfMonth('2024-02-17'), '2024-02-29');
  assert.deepEqual(startOfQuarter('2024-08-17'), '2024-07-01');
  assert.deepEqual(endOfQuarter('2024-08-17'), '2024-09-30');
  assert.deepEqual(startOfYear('2024-08-17'), '2024-01-01');
  assert.deepEqual(endOfYear('2024-08-17'), '2024-12-31');
});

test("weeks start on Monday unless told otherwise", () => {
  assert.deepEqual(startOfWeek('2024-03-06'), '2024-03-04');
  assert.deepEqual(startOfWeek('2024-03-10'), '2024-03-04');
  assert.deepEqual(startOfWeek('2024-03-06', { weekStart: 'SU' }), '2024-03-03');
  assert.deepEqual(endOfWeek('2024-03-06'), '2024-03-10');
  assert.deepEqual(endOfWeek('2024-03-06', { weekStart: 'SA' }), '2024-03-08');
  assert.throws(() => startOfWeek('2024-03-06', { weekStart: 'su' }), { code: 'E_RANGE' });
});

test("clamp, min, max, sort", () => {
  assert.deepEqual(clampDate('2024-05-01', '2024-01-01', '2024-03-31'), '2024-03-31');
  assert.throws(() => clampDate('2024-05-01', '2024-06-01', '2024-03-31'), { code: 'E_RANGE' });
  assert.deepEqual(minDate(['2024-05-01', '2023-12-31']), '2023-12-31');
  assert.throws(() => maxDate([]), { code: 'E_RANGE' });
  assert.deepEqual(sortDates(['2024-05-01', '2023-12-31', '2024-05-01']), ['2023-12-31', '2024-05-01', '2024-05-01']);
  assert.deepEqual(sortDates(['2024-05-01', '2023-12-31', '2024-05-01'], { descending: true, unique: true }), ['2024-05-01', '2023-12-31']);
});

test("eachDay is inclusive and never reversed", () => {
  assert.deepEqual(eachDay('2024-02-27', '2024-03-02'), ['2024-02-27', '2024-02-28', '2024-02-29', '2024-03-01', '2024-03-02']);
  assert.deepEqual(eachDay('2024-02-27', '2024-03-02', { step: 2 }), ['2024-02-27', '2024-02-29', '2024-03-02']);
  assert.deepEqual(eachDay('2024-03-02', '2024-02-27'), []);
  assert.throws(() => eachDay('2024-01-01', '2024-01-02', { step: 0 }), { code: 'E_RANGE' });
  assert.throws(() => eachDay('2000-01-01', '2020-01-01'), { code: 'E_LIMIT' });
});

test("misc helpers", () => {
  assert.deepEqual(isSameMonth('2024-02-01', '2024-02-29'), true);
  assert.deepEqual(isSameMonth('2024-02-01', '2023-02-01'), false);
  assert.deepEqual(isWeekend('2024-03-09'), true);
  assert.deepEqual(isWeekend('2024-03-08', { weekend: ['FR', 'SA'] }), true);
  assert.deepEqual(toCompact('2024-03-05'), '20240305');
  assert.deepEqual(dateParts('2024-03-05'), { year: 2024, month: 3, day: 5 });
});
