'use strict';

const assert = require('node:assert/strict');
const { test } = require('./harness');
const {
  ChronoError,
  dayInfo,
  dayKind,
  holidaysBetween,
  makeCalendar,
  mergeCalendars,
  workHours,
} = require('../src');

// a listed working date is a transfer even on an ordinary weekday; the day before a holiday is shortened
test("precedence of day kinds", () => {
  assert.deepEqual(dayKind('2024-05-08', { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), 'preholiday');
  assert.deepEqual(dayKind('2024-04-27', { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), 'transfer');
  assert.deepEqual(dayKind('2024-04-30', { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), 'preholiday');
  assert.deepEqual(dayKind('2024-05-09', { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), 'holiday');
  assert.deepEqual(dayKind('2024-05-11', { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), 'weekend');
  assert.deepEqual(dayKind('2024-05-03', { holidays: ['2024-05-03'], workdays: ['2024-05-03'] }), 'holiday');
  assert.deepEqual(dayKind('2024-05-07', { workdays: ['2024-05-07'] }), 'transfer');
  assert.deepEqual(dayKind('2024-05-03', null), 'workday');
});

test("work hours", () => {
  assert.deepEqual(workHours('2024-05-08', { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), 7);
  assert.deepEqual(workHours('2024-05-08', { holidays: ['2024-05-09'], dayHours: 7, preholidayCut: 2 }), 5);
  assert.deepEqual(workHours('2024-05-04', null), 0);
  assert.deepEqual(workHours('2024-05-03', { holidays: ['2024-05-06'] }), 8);
});

test("day info object", () => {
  assert.deepEqual(dayInfo('2024-04-27', { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), { date: '2024-04-27', kind: 'transfer', isWorkday: true, hours: 8, weekday: 'SA' });
  assert.deepEqual(dayInfo('2024-05-09', { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), { date: '2024-05-09', kind: 'holiday', isWorkday: false, hours: 0, weekday: 'TH' });
});

test("calendar validation", () => {
  assert.deepEqual(makeCalendar({ weekend: ['SU', 'SA', 'SU'] }), { weekend: ['SA', 'SU'], holidays: [], workdays: [], dayHours: 8, preholidayCut: 1 });
  assert.deepEqual(makeCalendar({ holidays: ['2024-01-02', '2024-01-01', '2024-01-02'], workdays: ['2024-01-01', '2024-01-06'] }), { weekend: ['SA', 'SU'], holidays: ['2024-01-01', '2024-01-02'], workdays: ['2024-01-06'], dayHours: 8, preholidayCut: 1 });
  assert.throws(() => makeCalendar({ holiday: ['2024-01-01'] }), { code: 'E_TYPE' });
  assert.throws(() => makeCalendar({ weekend: ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'] }), { code: 'E_RANGE' });
  assert.throws(() => makeCalendar({ dayHours: 0 }), { code: 'E_RANGE' });
  assert.throws(() => makeCalendar({ dayHours: 6, preholidayCut: 6 }), { code: 'E_RANGE' });
  assert.throws(() => makeCalendar({ dayHours: '8' }), { code: 'E_TYPE' });
  assert.throws(() => makeCalendar([]), { code: 'E_TYPE' });
  assert.deepEqual(makeCalendar(), { weekend: ['SA', 'SU'], holidays: [], workdays: [], dayHours: 8, preholidayCut: 1 });
});

test("holidays between and merging", () => {
  assert.deepEqual(holidaysBetween('2024-05-05', '2024-05-31', { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), ['2024-05-09', '2024-05-10']);
  assert.deepEqual(holidaysBetween('2024-05-31', '2024-05-05', { holidays: ['2024-05-01', '2024-05-09', '2024-05-10'], workdays: ['2024-04-27'] }), []);
  assert.deepEqual(mergeCalendars({ holidays: ['2024-05-09'] }, { holidays: ['2024-05-10'], workdays: ['2024-05-09'], dayHours: 7 }), { weekend: ['SA', 'SU'], holidays: ['2024-05-09', '2024-05-10'], workdays: [], dayHours: 7, preholidayCut: 1 });
  assert.deepEqual(mergeCalendars(null, { weekend: ['SU'] }), { weekend: ['SU'], holidays: [], workdays: [], dayHours: 8, preholidayCut: 1 });
  assert.throws(() => mergeCalendars({ preholidayCut: 3 }, { dayHours: 3 }), { code: 'E_RANGE' });
});
