'use strict';

const assert = require('node:assert/strict');
const { test } = require('./harness');
const {
  ChronoError,
  countOccurrences,
  expandRRule,
  nextOccurrence,
  occursOn,
  parseRRule,
  stringifyRRule,
} = require('../src');

test("parsing normalises the rule", () => {
  assert.deepEqual(parseRRule('rrule:freq=monthly;byday=+1mo,-1fr,1MO;bymonth=12,3'), { freq: 'MONTHLY', interval: 1, count: null, until: null, bymonth: [3, 12], bymonthday: [], byday: ['1MO', '-1FR'], bysetpos: [], wkst: 'MO' });
  assert.deepEqual(parseRRule('FREQ=DAILY;;COUNT=3;'), { freq: 'DAILY', interval: 1, count: 3, until: null, bymonth: [], bymonthday: [], byday: [], bysetpos: [], wkst: 'MO' });
  assert.deepEqual(parseRRule('FREQ=YEARLY;UNTIL=2024-12-31;BYMONTHDAY=31,-1,1,31'), { freq: 'YEARLY', interval: 1, count: null, until: '2024-12-31', bymonth: [], bymonthday: [-1, 1, 31], byday: [], bysetpos: [], wkst: 'MO' });
});

test("every problem is E_RULE", () => {
  assert.throws(() => parseRRule('FREQ=HOURLY'), { code: 'E_RULE' });
  assert.throws(() => parseRRule('FREQ = DAILY'), { code: 'E_RULE' });
  assert.throws(() => parseRRule('FREQ=DAILY;COUNT=3;UNTIL=20250101'), { code: 'E_RULE' });
  assert.throws(() => parseRRule('FREQ=YEARLY;UNTIL=20230230'), { code: 'E_RULE' });
  assert.throws(() => parseRRule('FREQ=WEEKLY;BYDAY=1MO'), { code: 'E_RULE' });
  assert.throws(() => parseRRule('FREQ=MONTHLY;BYDAY=-6FR'), { code: 'E_RULE' });
  assert.deepEqual(parseRRule('FREQ=YEARLY;BYDAY=20MO'), { freq: 'YEARLY', interval: 1, count: null, until: null, bymonth: [], bymonthday: [], byday: ['20MO'], bysetpos: [], wkst: 'MO' });
  assert.throws(() => parseRRule('FREQ=YEARLY;BYMONTH=1;BYDAY=20MO'), { code: 'E_RULE' });
  assert.throws(() => parseRRule('FREQ=MONTHLY;BYDAY=-1FR;BYMONTHDAY=13'), { code: 'E_RULE' });
  assert.throws(() => parseRRule('FREQ=DAILY;BYSETPOS=1'), { code: 'E_RULE' });
  assert.throws(() => parseRRule('FREQ=DAILY;INTERVAL=+2'), { code: 'E_RULE' });
  assert.throws(() => parseRRule(null), { code: 'E_TYPE' });
});

test("canonical text", () => {
  assert.deepEqual(stringifyRRule('byday=fr,mo;freq=weekly;interval=1;wkst=mo'), 'FREQ=WEEKLY;BYDAY=FR,MO');
  assert.deepEqual(stringifyRRule('UNTIL=2024-06-30;FREQ=MONTHLY;BYMONTHDAY=-1,15;WKST=SU'), 'FREQ=MONTHLY;UNTIL=20240630;BYMONTHDAY=-1,15;WKST=SU');
  assert.deepEqual(stringifyRRule({ freq: 'monthly', byday: ['-1FR'], count: 3 }), 'FREQ=MONTHLY;COUNT=3;BYDAY=-1FR');
  assert.throws(() => stringifyRRule({ freq: 'MONTHLY', interval: '2' }), { code: 'E_TYPE' });
  assert.throws(() => stringifyRRule({ freq: 'MONTHLY', every: 2 }), { code: 'E_TYPE' });
});

test("dtstart is only an occurrence when it matches", () => {
  assert.deepEqual(expandRRule('FREQ=MONTHLY;BYDAY=-1FR;COUNT=2', '2024-03-01'), ['2024-03-29', '2024-04-26']);
  assert.deepEqual(expandRRule('FREQ=MONTHLY;BYDAY=-1FR;COUNT=2', '2024-03-29'), ['2024-03-29', '2024-04-26']);
  assert.deepEqual(expandRRule('FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH;COUNT=4', '2024-03-06'), ['2024-03-07', '2024-03-18', '2024-03-21', '2024-04-01']);
});

test("monthly rules skip months without the day", () => {
  assert.deepEqual(expandRRule('FREQ=MONTHLY', '2024-01-31', { limit: 4 }), ['2024-01-31', '2024-03-31', '2024-05-31', '2024-07-31']);
  assert.deepEqual(expandRRule('FREQ=YEARLY;COUNT=3', '2024-02-29'), ['2024-02-29', '2028-02-29', '2032-02-29']);
  assert.deepEqual(expandRRule('FREQ=MONTHLY;BYMONTHDAY=30,-1;COUNT=6', '2024-01-01'), ['2024-01-30', '2024-01-31', '2024-02-29', '2024-03-30', '2024-03-31', '2024-04-30']);
});

test("yearly BYDAY without BYMONTH counts in the whole year", () => {
  assert.deepEqual(expandRRule('FREQ=YEARLY;BYDAY=20MO;COUNT=2', '2024-01-01'), ['2024-05-13', '2025-05-19']);
  assert.deepEqual(expandRRule('FREQ=YEARLY;BYDAY=-1SU;COUNT=2', '2024-01-01'), ['2024-12-29', '2025-12-28']);
  assert.deepEqual(expandRRule('FREQ=YEARLY;BYMONTH=5;BYDAY=-1MO;COUNT=2', '2024-01-01'), ['2024-05-27', '2025-05-26']);
});

test("BYSETPOS picks inside each period", () => {
  assert.deepEqual(expandRRule('FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=4', '2024-01-01'), ['2024-01-31', '2024-02-29', '2024-03-29', '2024-04-30']);
  assert.deepEqual(expandRRule('FREQ=MONTHLY;BYDAY=SA,SU;BYSETPOS=1,2,9;COUNT=4', '2024-06-01'), ['2024-06-01', '2024-06-02', '2024-06-29', '2024-07-06']);
});

test("WKST moves weekly periods", () => {
  assert.deepEqual(expandRRule('FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,SU;COUNT=4', '2024-03-05'), ['2024-03-10', '2024-03-18', '2024-03-24', '2024-04-01']);
  assert.deepEqual(expandRRule('FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,SU;COUNT=4;WKST=SU', '2024-03-05'), ['2024-03-17', '2024-03-18', '2024-03-31', '2024-04-01']);
});

test("windows and limits", () => {
  assert.deepEqual(expandRRule('FREQ=DAILY;INTERVAL=10', '2024-01-01', { after: '2024-01-11', before: '2024-02-10' }), ['2024-01-11', '2024-01-21', '2024-01-31', '2024-02-10']);
  assert.deepEqual(expandRRule('FREQ=DAILY;INTERVAL=10', '2024-01-01', { after: '2024-01-11', before: '2024-02-10', inclusive: false }), ['2024-01-21', '2024-01-31']);
  assert.deepEqual(expandRRule('FREQ=DAILY;COUNT=5', '2024-01-01', { after: '2024-01-04' }), ['2024-01-04', '2024-01-05']);
  assert.throws(() => expandRRule('FREQ=DAILY', '2024-01-01'), { code: 'E_LIMIT' });
  assert.throws(() => expandRRule('FREQ=DAILY', '2024-01-01', { limit: 0 }), { code: 'E_RANGE' });
  assert.deepEqual(expandRRule('FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30', '2024-01-01', { limit: 3 }), []);
});

test("next, occurs, count", () => {
  assert.deepEqual(nextOccurrence('FREQ=MONTHLY;BYDAY=2TU', '2024-01-01', '2024-05-14'), '2024-06-11');
  assert.deepEqual(nextOccurrence('FREQ=DAILY;COUNT=3', '2024-01-01', '2024-01-03'), null);
  assert.deepEqual(occursOn('FREQ=WEEKLY;INTERVAL=2;BYDAY=FR', '2024-03-01', '2024-03-15'), true);
  assert.deepEqual(occursOn('FREQ=WEEKLY;INTERVAL=2;BYDAY=FR', '2024-03-01', '2024-03-08'), false);
  assert.deepEqual(countOccurrences('FREQ=MONTHLY;UNTIL=20241231', '2024-01-31'), 7);
  assert.deepEqual(countOccurrences('FREQ=WEEKLY', '2024-01-01', { before: '2024-01-31' }), 5);
  assert.throws(() => countOccurrences('FREQ=WEEKLY', '2024-01-01'), { code: 'E_LIMIT' });
});
