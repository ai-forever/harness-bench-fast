'use strict';

/**
 * @module period
 *
 * Reporting periods and their labels.
 *
 * | kind      | label        | example    | covers                                  |
 * |-----------|--------------|------------|-----------------------------------------|
 * | `year`    | `YYYY`       | `2024`     | 1 Jan – 31 Dec                          |
 * | `half`    | `YYYY-Hn`    | `2024-H2`  | Jan–Jun / Jul–Dec                        |
 * | `quarter` | `YYYY-Qn`    | `2024-Q3`  | three calendar months                   |
 * | `month`   | `YYYY-MM`    | `2024-05`  | one calendar month                      |
 * | `week`    | `YYYY-Www`   | `2024-W05` | an ISO week, Monday–Sunday (ISO week-year!) |
 * | `fiscal`  | `FYYYYY`     | `FY2025`   | twelve months starting in `fiscalStart` |
 *
 * Labels are case sensitive (`2024-q3` is `E_PARSE`) and never padded or
 * trimmed. A label of a recognised shape with an impossible number
 * (`2024-Q5`, `2024-13`, `2024-H3`, `2020-W54`, `2021-W53`) is `E_RANGE`; a
 * week number is checked against {@link isoWeeksInYear}. Years must be
 * 1000..9999.
 *
 * ## Fiscal years
 *
 * The option `fiscalStart` (month 1..12, default 1) says in which month a
 * fiscal year begins. **A fiscal year is named after the calendar year in
 * which it ends**: with `fiscalStart: 7`, the period 1 July 2024 – 30 June 2025
 * is `FY2025`. With `fiscalStart: 1` a fiscal year equals the calendar year
 * of the same number.
 *
 * Every function accepting `fiscalStart` validates it (`E_TYPE`/`E_RANGE`)
 * even when the label or kind at hand is not fiscal.
 */

const { daysFromCivil, dim, iso, needInt, needMonth, needStr, toDays, ymd, div, pad } = require('./internal');
const { fail, E_LIMIT, E_PARSE, E_RANGE } = require('./errors');
const { isoWeekOf, isoWeekStart, isoWeeksInYear } = require('./weeks');

const KINDS = Object.freeze(['year', 'half', 'quarter', 'month', 'week', 'fiscal']);
const MAX_PERIODS = 1000;

/** Label shapes, tried in this order. */
const PATTERNS = [
  ['year', /^([0-9]{4})$/],
  ['half', /^([0-9]{4})-H([0-9])$/],
  ['quarter', /^([0-9]{4})-Q([0-9])$/],
  ['month', /^([0-9]{4})-([0-9]{2})$/],
  ['week', /^([0-9]{4})-W([0-9]{2})$/],
  ['fiscal', /^FY([0-9]{4})$/],
];
const PER_YEAR = { half: 2, quarter: 4, month: 12 };

function yearOk(y) {
  if (y < 1000 || y > 9999) fail(E_RANGE, 'year out of range');
  return y;
}

/**
 * Parse a label into `[kind, year, index]`; index is 0 for year and fiscal
 * labels, otherwise the half/quarter/month/week number.
 * @param {string} label
 * @returns {[string, number, number]}
 */
function parseLabel(label) {
  needStr(label, 'label');
  for (const [kind, rx] of PATTERNS) {
    const mt = rx.exec(label);
    if (!mt) continue;
    const y = yearOk(parseInt(mt[1], 10));
    if (kind === 'year' || kind === 'fiscal') return [kind, y, 0];
    const x = parseInt(mt[2], 10);
    const top = kind === 'week' ? isoWeeksInYear(y) : PER_YEAR[kind];
    if (x < 1 || x > top) fail(E_RANGE, `${kind} number out of range`);
    return [kind, y, x];
  }
  return fail(E_PARSE, 'unrecognised period label');
}

/** Build a label; the year is range-checked. */
function makeLabel(kind, y, x) {
  yearOk(y);
  if (kind === 'year') return pad(y, 4);
  if (kind === 'half') return `${pad(y, 4)}-H${x}`;
  if (kind === 'quarter') return `${pad(y, 4)}-Q${x}`;
  if (kind === 'month') return `${pad(y, 4)}-${pad(x, 2)}`;
  if (kind === 'week') return `${pad(y, 4)}-W${pad(x, 2)}`;
  return `FY${pad(y, 4)}`;
}

function needKind(kind) {
  needStr(kind, 'kind');
  if (!KINDS.includes(kind)) fail(E_RANGE, 'unknown period kind');
  return kind;
}

/**
 * Calendar quarter 1..4.
 * @param {string} date
 * @returns {number}
 */
function quarterOf(date) {
  return div(ymd(toDays(date))[1] - 1, 3) + 1;
}

/**
 * Half-year 1 or 2.
 * @param {string} date
 * @returns {number}
 */
function halfOf(date) {
  return ymd(toDays(date))[1] <= 6 ? 1 : 2;
}

/**
 * Fiscal year number of a date (named after the year in which it ends).
 *
 * @param {string} date
 * @param {number} startMonth 1..12 (required, positional)
 * @returns {number}
 * @example fiscalYear('2024-06-30', 7) // 2024
 * @example fiscalYear('2024-07-01', 7) // 2025
 * @example fiscalYear('2024-07-01', 1) // 2024
 */
function fiscalYear(date, startMonth) {
  const n = toDays(date);
  needMonth(startMonth);
  const [y, m] = ymd(n);
  if (startMonth === 1) return y;
  return m >= startMonth ? y + 1 : y;
}

/** [year, index] of the period of `kind` containing day number n. */
function periodParts(n, kind, fs) {
  const [y, m] = ymd(n);
  if (kind === 'year') return [y, 0];
  if (kind === 'half') return [y, m <= 6 ? 1 : 2];
  if (kind === 'quarter') return [y, div(m - 1, 3) + 1];
  if (kind === 'month') return [y, m];
  if (kind === 'week') return isoWeekOf(n);
  return [fs === 1 ? y : m >= fs ? y + 1 : y, 0];
}

/**
 * Label of the period of the given kind that contains `date`.
 *
 * @param {string} date
 * @param {string} kind one of year, half, quarter, month, week, fiscal
 * @param {object} [options]
 * @param {number} [options.fiscalStart=1]
 * @returns {string}
 * @example periodOf('2021-01-03', 'week')                       // '2020-W53'
 * @example periodOf('2024-08-15', 'fiscal', { fiscalStart: 4 }) // 'FY2025'
 */
function periodOf(date, kind, { fiscalStart = 1 } = {}) {
  const n = toDays(date);
  needKind(kind);
  needMonth(fiscalStart);
  const [y, x] = periodParts(n, kind, fiscalStart);
  return makeLabel(kind, y, x);
}

/** [firstDay, lastDay] day numbers of a parsed label. */
function rangeDays(kind, y, x, fs) {
  if (kind === 'year') return [daysFromCivil(y, 1, 1), daysFromCivil(y, 12, 31)];
  if (kind === 'half') {
    const m0 = x === 1 ? 1 : 7;
    return [daysFromCivil(y, m0, 1), daysFromCivil(y, m0 + 5, dim(y, m0 + 5))];
  }
  if (kind === 'quarter') {
    const m0 = (x - 1) * 3 + 1;
    return [daysFromCivil(y, m0, 1), daysFromCivil(y, m0 + 2, dim(y, m0 + 2))];
  }
  if (kind === 'month') return [daysFromCivil(y, x, 1), daysFromCivil(y, x, dim(y, x))];
  if (kind === 'week') {
    const start = toDays(isoWeekStart(y, x));
    return [start, start + 6];
  }
  if (fs === 1) return [daysFromCivil(y, 1, 1), daysFromCivil(y, 12, 31)];
  return [daysFromCivil(y - 1, fs, 1), daysFromCivil(y, fs, 1) - 1];
}

/**
 * First and last day of a period, both inclusive.
 *
 * @param {string} label
 * @param {object} [options]
 * @param {number} [options.fiscalStart=1]
 * @returns {[string, string]}
 * @example periodRange('2024-Q1')                      // ['2024-01-01', '2024-03-31']
 * @example periodRange('FY2025', { fiscalStart: 7 })   // ['2024-07-01', '2025-06-30']
 * @example periodRange('2025-W01')                     // ['2024-12-30', '2025-01-05']
 */
function periodRange(label, { fiscalStart = 1 } = {}) {
  const [kind, y, x] = parseLabel(label);
  needMonth(fiscalStart);
  const [a, b] = rangeDays(kind, y, x, fiscalStart);
  return [iso(a), iso(b)];
}

/**
 * Kind of a label (`'year'`, `'half'`, ...). Validates the label fully.
 * @param {string} label
 * @returns {string}
 */
function periodKind(label) {
  return parseLabel(label)[0];
}

/**
 * The label `n` periods of the same kind later (earlier for negative n).
 * Weeks are shifted by 7·n days from the ISO week start, so the week-year
 * rolls correctly (`shiftPeriod('2020-W53', 1) === '2021-W01'`).
 *
 * @param {string} label
 * @param {number} n
 * @param {object} [options]
 * @param {number} [options.fiscalStart=1]
 * @returns {string}
 * @example shiftPeriod('2024-Q4', 1)  // '2025-Q1'
 * @example shiftPeriod('2024-01', -13) // '2022-12'
 */
function shiftPeriod(label, n, { fiscalStart = 1 } = {}) {
  const [kind, y, x] = parseLabel(label);
  needInt(n, 'n');
  needMonth(fiscalStart);
  if (kind === 'year' || kind === 'fiscal') return makeLabel(kind, y + n, 0);
  if (kind === 'week') {
    const start = toDays(isoWeekStart(y, x)) + 7 * n;
    const [wy, wn] = isoWeekOf(start);
    return makeLabel('week', wy, wn);
  }
  const per = PER_YEAR[kind];
  const total = y * per + (x - 1) + n;
  const ny = div(total, per);
  return makeLabel(kind, ny, total - ny * per + 1);
}

/**
 * Labels of all periods of `kind` that intersect `[start, end]`, in order.
 * Empty when end < start; `E_LIMIT` above 1000 labels.
 *
 * @param {string} start
 * @param {string} end
 * @param {string} kind
 * @param {object} [options]
 * @param {number} [options.fiscalStart=1]
 * @returns {string[]}
 */
function periodsBetween(start, end, kind, { fiscalStart = 1 } = {}) {
  const a = toDays(start);
  const b = toDays(end);
  needKind(kind);
  needMonth(fiscalStart);
  if (b < a) return [];
  const first = makeLabel(kind, ...periodParts(a, kind, fiscalStart));
  const last = makeLabel(kind, ...periodParts(b, kind, fiscalStart));
  const out = [first];
  let cur = first;
  while (cur !== last) {
    cur = shiftPeriod(cur, 1, { fiscalStart });
    out.push(cur);
    if (out.length > MAX_PERIODS) fail(E_LIMIT, 'too many periods');
  }
  return out;
}

/**
 * Does the period contain the date?
 *
 * @param {string} label
 * @param {string} date
 * @param {object} [options]
 * @param {number} [options.fiscalStart=1]
 * @returns {boolean}
 */
function periodContains(label, date, { fiscalStart = 1 } = {}) {
  const [kind, y, x] = parseLabel(label);
  const n = toDays(date);
  needMonth(fiscalStart);
  const [a, b] = rangeDays(kind, y, x, fiscalStart);
  return a <= n && n <= b;
}

/**
 * Length of the period in days.
 *
 * @param {string} label
 * @param {object} [options]
 * @param {number} [options.fiscalStart=1]
 * @returns {number}
 */
function daysInPeriod(label, { fiscalStart = 1 } = {}) {
  const [kind, y, x] = parseLabel(label);
  needMonth(fiscalStart);
  const [a, b] = rangeDays(kind, y, x, fiscalStart);
  return b - a + 1;
}

module.exports = {
  KINDS,
  parseLabel,
  quarterOf,
  halfOf,
  fiscalYear,
  periodOf,
  periodRange,
  periodKind,
  shiftPeriod,
  periodsBetween,
  periodContains,
  daysInPeriod,
};
