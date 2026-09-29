'use strict';

const assert = require('node:assert/strict');
const { test } = require('./harness');
const {
  ChronoError,
  scheduleBetween,
  scheduleNext,
  scheduleTake,
} = require('../src');

// EXDATEs match raw dates before rolling; COUNT is not refilled
test("pipeline order: rule, rdates, exdates, roll, dedupe", () => {
  assert.deepEqual(scheduleBetween({ rule: 'FREQ=MONTHLY;BYMONTHDAY=31', start: '2024-01-01', roll: 'modified_following' }, '2024-08-01', '2024-09-30'), ['2024-08-30']);
  assert.deepEqual(scheduleBetween({ rule: 'FREQ=MONTHLY;BYMONTHDAY=31', start: '2024-01-01', roll: 'modified_following', exdates: ['2024-03-31'] }, '2024-01-01', '2024-06-30'), ['2024-01-31', '2024-05-31']);
  assert.deepEqual(scheduleBetween({ rule: 'FREQ=DAILY;COUNT=6', start: '2024-05-07', roll: 'following', calendar: { holidays: ['2024-05-09', '2024-05-10'] } }, '2024-05-01', '2024-05-31'), ['2024-05-07', '2024-05-08', '2024-05-13']);
  assert.deepEqual(scheduleBetween({ rule: 'FREQ=DAILY;COUNT=6', start: '2024-05-07', roll: 'following', calendar: { holidays: ['2024-05-09', '2024-05-10'] }, exdates: ['2024-05-13'] }, '2024-05-01', '2024-05-31'), ['2024-05-07', '2024-05-08', '2024-05-13']);
  assert.deepEqual(scheduleBetween({ rule: 'FREQ=WEEKLY;BYDAY=MO;COUNT=3', start: '2024-03-01', rdates: ['2024-02-01', '2024-03-06', '2024-03-04'] }, '2024-01-01', '2024-12-31'), ['2024-03-04', '2024-03-06', '2024-03-11', '2024-03-18']);
});

test("the window cuts raw dates at the end", () => {
  assert.deepEqual(scheduleBetween({ rule: 'FREQ=WEEKLY;BYDAY=SA', start: '2024-03-01', roll: 'preceding' }, '2024-03-01', '2024-03-15'), ['2024-03-01', '2024-03-08']);
  assert.deepEqual(scheduleBetween({ rule: 'FREQ=WEEKLY;BYDAY=SA', start: '2024-03-01', roll: 'preceding' }, '2024-03-01', '2024-03-16'), ['2024-03-01', '2024-03-08', '2024-03-15']);
});

test("take and next", () => {
  assert.deepEqual(scheduleTake({ rule: 'FREQ=MONTHLY;BYDAY=-1FR', start: '2024-01-01', roll: 'preceding' }, 3, { from: '2024-05-01' }), ['2024-05-31', '2024-06-28', '2024-07-26']);
  assert.deepEqual(scheduleTake({ rule: 'FREQ=DAILY;COUNT=2', start: '2024-01-01', rdates: ['2024-02-01'] }, 5), ['2024-01-01', '2024-01-02', '2024-02-01']);
  assert.throws(() => scheduleTake({ rule: 'FREQ=DAILY', start: '2024-01-01' }, 0), { code: 'E_RANGE' });
  assert.deepEqual(scheduleNext({ rule: 'FREQ=MONTHLY;BYMONTHDAY=25', start: '2024-01-01', roll: 'following' }, '2024-05-25'), '2024-05-27');
  assert.deepEqual(scheduleNext({ rule: 'FREQ=DAILY;COUNT=1', start: '2024-01-01' }, '2024-01-01'), null);
});

test("spec validation", () => {
  assert.throws(() => scheduleTake({ rule: 'FREQ=DAILY', start: '2024-01-01', exdate: [] }, 1), { code: 'E_TYPE' });
  assert.throws(() => scheduleTake({ rule: 'FREQ=DAILY' }, 1), { code: 'E_TYPE' });
  assert.throws(() => scheduleTake({ rule: 'FREQ=DAILY', start: '2024-01-01', roll: 'Following' }, 1), { code: 'E_RANGE' });
});
