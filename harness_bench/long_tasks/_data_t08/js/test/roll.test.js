'use strict';

const assert = require('node:assert/strict');
const { test } = require('./harness');
const {
  ChronoError,
  isRolled,
  roll,
  rollMany,
} = require('../src');

test("conventions", () => {
  assert.deepEqual(roll('2024-03-31', 'following'), '2024-04-01');
  assert.deepEqual(roll('2024-03-31', 'modified_following'), '2024-03-29');
  assert.deepEqual(roll('2024-06-01', 'preceding'), '2024-05-31');
  assert.deepEqual(roll('2024-06-01', 'modified_preceding'), '2024-06-03');
  assert.deepEqual(roll('2024-03-09', 'nearest'), '2024-03-08');
  assert.deepEqual(roll('2024-03-10', 'nearest'), '2024-03-11');
  assert.deepEqual(roll('2024-03-11', 'preceding'), '2024-03-11');
  assert.deepEqual(roll('2024-03-10', 'none'), '2024-03-10');
  assert.throws(() => roll('2024-03-10', 'modifiedFollowing'), { code: 'E_RANGE' });
  assert.throws(() => roll('2024-03-10', 5), { code: 'E_TYPE' });
});

test("nearest prefers the later day on a tie", () => {
  assert.deepEqual(roll('2024-05-10', 'nearest', { holidays: ['2024-05-10'] }), '2024-05-09');
  assert.deepEqual(roll('2024-03-10', 'nearest', { holidays: ['2024-03-11'] }), '2024-03-12');
  assert.deepEqual(roll('2024-03-09', 'nearest', { holidays: ['2024-03-08'] }), '2024-03-11');
});

test("rollMany keeps the first date that lands on a day", () => {
  assert.deepEqual(rollMany(['2024-03-09', '2024-03-10', '2024-03-11'], 'following'), ['2024-03-11']);
  assert.deepEqual(rollMany(['2024-03-09', '2024-03-10', '2024-03-11'], 'following', null, { dedupe: false }), ['2024-03-11', '2024-03-11', '2024-03-11']);
  assert.deepEqual(rollMany(['2024-03-11', '2024-03-09'], 'preceding'), ['2024-03-11', '2024-03-08']);
  assert.deepEqual(isRolled('2024-03-09', 'following'), true);
  assert.deepEqual(isRolled('2024-03-08', 'following'), false);
});
