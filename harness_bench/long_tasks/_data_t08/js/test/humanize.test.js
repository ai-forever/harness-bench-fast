'use strict';

const assert = require('node:assert/strict');
const { test } = require('./harness');
const {
  ChronoError,
  describeDate,
  describeDayRange,
  describeDuration,
  describePeriod,
  describeRelative,
  describeRule,
} = require('../src');

test("rule heads", () => {
  assert.deepEqual(describeRule('FREQ=DAILY', '2024-01-01'), 'каждый день');
  assert.deepEqual(describeRule('FREQ=DAILY;INTERVAL=21', '2024-01-01'), 'каждый 21 день');
  assert.deepEqual(describeRule('FREQ=WEEKLY;INTERVAL=21', '2024-01-01'), 'каждую 21 неделю по понедельникам');
  assert.deepEqual(describeRule('FREQ=YEARLY;INTERVAL=5', '2024-03-08'), 'каждые 5 лет 8 марта');
  assert.deepEqual(describeRule('FREQ=YEARLY;INTERVAL=22', '2024-03-08'), 'каждые 22 года 8 марта');
  assert.deepEqual(describeRule('FREQ=MONTHLY;INTERVAL=12', '2024-03-08'), 'каждые 12 месяцев 8-го числа');
});

test("rule details", () => {
  assert.deepEqual(describeRule('FREQ=WEEKLY;INTERVAL=2;BYDAY=FR,MO', '2024-01-01'), 'каждые 2 недели по понедельникам и пятницам');
  assert.deepEqual(describeRule('FREQ=MONTHLY;BYDAY=-1FR;COUNT=12', '2024-01-01'), 'каждый месяц в последнюю пятницу, 12 раз');
  assert.deepEqual(describeRule('FREQ=MONTHLY;BYDAY=1WE,-2SU,3SA', '2024-01-01'), 'каждый месяц в первую среду, предпоследнее воскресенье и третью субботу');
  assert.deepEqual(describeRule('FREQ=MONTHLY;BYDAY=-3FR', '2024-01-01'), 'каждый месяц в 3-ю пятницу с конца');
  assert.deepEqual(describeRule('FREQ=YEARLY;BYDAY=20MO', '2024-01-01'), 'каждый год в 20-й понедельник года');
  assert.deepEqual(describeRule('FREQ=MONTHLY;BYMONTHDAY=1,15,-1,-2', '2024-01-01'), 'каждый месяц 2-го с конца, последнего, 1-го и 15-го числа');
  assert.deepEqual(describeRule('FREQ=YEARLY;BYMONTH=6,3', '2024-01-31'), 'каждый год 31-го числа в марте и июне');
  assert.deepEqual(describeRule('FREQ=DAILY;BYMONTH=12;BYDAY=SA,SU', '2024-01-01'), 'каждый день по субботам и воскресеньям в декабре');
  assert.deepEqual(describeRule('FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1,1;UNTIL=20241231', '2024-01-01'), 'каждый месяц по понедельникам, вторникам, средам, четвергам и пятницам (позиции -1, 1), до 31 декабря 2024 г.');
  assert.deepEqual(describeRule('FREQ=WEEKLY;COUNT=22', '2024-01-07'), 'каждую неделю по воскресеньям, 22 раза');
});

test("durations and periods", () => {
  assert.deepEqual(describeDuration('P1Y2M3W1D'), '1 год 2 месяца 3 недели 1 день');
  assert.deepEqual(describeDuration('-P5Y'), 'минус 5 лет');
  assert.deepEqual(describeDuration('P0D'), '0 дней');
  assert.deepEqual(describeDuration({ weeks: 21, days: 12 }), '21 неделя 12 дней');
  assert.deepEqual(describePeriod('2024'), '2024 год');
  assert.deepEqual(describePeriod('2024-H1'), '1-е полугодие 2024 г.');
  assert.deepEqual(describePeriod('2024-Q3'), 'III квартал 2024 г.');
  assert.deepEqual(describePeriod('2024-05'), 'май 2024 г.');
  assert.deepEqual(describePeriod('2024-W05'), '5-я неделя 2024 г.');
  assert.deepEqual(describePeriod('FY2025'), '2025 финансовый год');
  assert.deepEqual(describePeriod('FY2025', { fiscalStart: 7 }), '2025 финансовый год (с 1 июля 2024 г.)');
});

test("dates and relative dates", () => {
  assert.deepEqual(describeDate('2024-03-05'), 'вторник, 5 марта 2024 г.');
  assert.deepEqual(describeRelative('2024-01-15', '2024-01-01'), 'через 2 недели');
  assert.deepEqual(describeRelative('2024-01-08', '2024-01-01'), 'через 1 неделю');
  assert.deepEqual(describeRelative('2023-12-11', '2024-01-01'), '3 недели назад');
  assert.deepEqual(describeRelative('2024-03-04', '2024-01-01'), 'через 63 дня');
  assert.deepEqual(describeRelative('2024-01-22', '2024-01-01'), 'через 3 недели');
  assert.deepEqual(describeRelative('2023-12-30', '2024-01-01'), 'позавчера');
  assert.deepEqual(describeDayRange('2024-03-05', '2024-03-07'), '3 дня: с 5 марта 2024 г. по 7 марта 2024 г.');
  assert.deepEqual(describeDayRange('2024-03-05', '2024-03-25'), '21 день: с 5 марта 2024 г. по 25 марта 2024 г.');
});
