'use strict';

const assert = require('node:assert/strict');
const { test } = require('./harness');
const {
  ChronoError,
  parseDate,
  tryParseDate,
} = require('../src');

test("ISO and compact forms", () => {
  assert.deepEqual(parseDate('2024-03-05'), '2024-03-05');
  assert.deepEqual(parseDate('20240305'), '2024-03-05');
  assert.deepEqual(parseDate('  2024-03-05\n'), '2024-03-05');
  assert.throws(() => parseDate('2024-3-5'), { code: 'E_PARSE' });
  assert.throws(() => parseDate('2024-02-30'), { code: 'E_RANGE' });
});

test("dotted day-first dates with the pivot year 50", () => {
  assert.deepEqual(parseDate('5.3.2024'), '2024-03-05');
  assert.deepEqual(parseDate('05.03.24'), '2024-03-05');
  assert.deepEqual(parseDate('01.02.49'), '2049-02-01');
  assert.deepEqual(parseDate('01.02.50'), '1950-02-01');
  assert.throws(() => parseDate('1.1.124'), { code: 'E_PARSE' });
  assert.throws(() => parseDate('29.02.23'), { code: 'E_RANGE' });
});

test("slashes", () => {
  assert.deepEqual(parseDate('2024/3/5'), '2024-03-05');
  assert.deepEqual(parseDate('5/3/2024'), '2024-03-05');
  assert.deepEqual(parseDate('3/5/2024', { dayFirst: false }), '2024-03-05');
  assert.throws(() => parseDate('13/13/2024'), { code: 'E_RANGE' });
  assert.throws(() => parseDate('5/3/24'), { code: 'E_PARSE' });
});

test("Russian month words", () => {
  assert.deepEqual(parseDate('5 марта 2024'), '2024-03-05');
  assert.deepEqual(parseDate('5 март 2024'), '2024-03-05');
  assert.deepEqual(parseDate('05 мар. 2024 г.'), '2024-03-05');
  assert.deepEqual(parseDate('5 МАРТА 2024 Г.'), '2024-03-05');
  assert.deepEqual(parseDate('5 марта 2024г'), '2024-03-05');
  assert.deepEqual(parseDate('1 сент. 2024'), '2024-09-01');
  assert.throws(() => parseDate('5 мартобря 2024'), { code: 'E_PARSE' });
  assert.throws(() => parseDate('5 march 2024'), { code: 'E_PARSE' });
  assert.throws(() => parseDate('5\tмарта 2024'), { code: 'E_PARSE' });
});

test("tryParseDate swallows ChronoError", () => {
  assert.deepEqual(tryParseDate('31.02.2024'), null);
  assert.deepEqual(tryParseDate(42), null);
  assert.deepEqual(tryParseDate('8 мая 2024'), '2024-05-08');
});
