'use strict';

const assert = require('node:assert/strict');
const { test } = require('./harness');
const {
  ChronoError,
  formatDate,
  formatRange,
  monthName,
  pluralRu,
  weekdayName,
} = require('../src');

// DDD is DD followed by D; a lone d is a literal
test("tokens", () => {
  assert.deepEqual(formatDate('2024-03-05', 'D MMMM YYYY [г.]'), '5 марта 2024 г.');
  assert.deepEqual(formatDate('2024-03-05', 'dd, DD.MM.YY'), 'вт, 05.03.24');
  assert.deepEqual(formatDate('2021-01-03', 'GGGG-[W]WW-E'), '2020-W53-7');
  assert.deepEqual(formatDate('2024-05-09', 'D MMM, LLLL, dddd'), '9 мая, май, четверг');
  assert.deepEqual(formatDate('2024-03-05', 'DDDD / DDD'), '065 / 055');
  assert.deepEqual(formatDate('2024-03-05', 'Q кв. YYYY, неделя W'), '1 кв. 2024, неделя 10');
  assert.deepEqual(formatDate('2024-03-05', '[не закрыто'), '[не закрыто');
  assert.deepEqual(formatDate('2024-03-05', 'YYYYY MMMMM ddd'), '2024Y марта3 втd');
});

test("ranges", () => {
  assert.deepEqual(formatRange('2024-03-05', '2024-03-05'), '5 марта 2024');
  assert.deepEqual(formatRange('2024-03-05', '2024-03-09'), '5–9 марта 2024');
  assert.deepEqual(formatRange('2024-02-28', '2024-03-03'), '28 февраля – 3 марта 2024');
  assert.deepEqual(formatRange('2024-12-30', '2025-01-02'), '30 декабря 2024 – 2 января 2025');
  assert.throws(() => formatRange('2024-03-05', '2024-03-04'), { code: 'E_RANGE' });
});

test("plurals and names", () => {
  assert.deepEqual(pluralRu(21, 'день', 'дня', 'дней'), 'день');
  assert.deepEqual(pluralRu(12, 'день', 'дня', 'дней'), 'дней');
  assert.deepEqual(pluralRu(-22, 'день', 'дня', 'дней'), 'дня');
  assert.deepEqual(pluralRu(111, 'день', 'дня', 'дней'), 'дней');
  assert.deepEqual(monthName(5, { form: 'genitive' }), 'мая');
  assert.deepEqual(monthName(5, { form: 'short' }), 'мая');
  assert.throws(() => monthName(5, { form: 'dative' }), { code: 'E_RANGE' });
  assert.deepEqual(weekdayName('WE', { form: 'accusative' }), 'среду');
  assert.deepEqual(weekdayName('SU', { form: 'dative_plural' }), 'воскресеньям');
  assert.deepEqual(weekdayName('SU'), 'воскресенье');
});
