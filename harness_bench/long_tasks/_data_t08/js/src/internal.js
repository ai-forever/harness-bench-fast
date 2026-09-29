'use strict';

/**
 * @module internal
 *
 * Low level helpers shared by all modules. Nothing here is exported from the
 * package entry point; the functions are documented anyway because every
 * public function relies on their exact behaviour.
 *
 * ## Day numbers
 *
 * Internally a date is an integer "day number": the count of days since
 * 1970-01-01 (which is day 0, a Thursday). Negative numbers are days before
 * 1970. The conversion uses the proleptic Gregorian calendar via Howard
 * Hinnant's `days_from_civil` / `civil_from_days` algorithms. We never use
 * the JavaScript `Date` object: it drags time zones and DST into what is a
 * purely civil (wall calendar) problem, and the old planning service lost a
 * day twice a year because of it.
 *
 * All arithmetic is integer arithmetic. Division is always floor division
 * (`Math.floor(a / b)`) and remainders go through {@link mod}, which returns
 * a non-negative result for a positive divisor (unlike the `%` operator).
 *
 * ## Public representation
 *
 * The public API speaks strings only: a date is `"YYYY-MM-DD"` with exactly
 * four digits of year and two digits of month and day. Anything else passed
 * where a date is expected is rejected:
 *
 * - not a string → `E_TYPE`;
 * - a string of another shape (`"2024-5-1"`, `"2024-05-01T00:00"`,
 *   `" 2024-05-01"`) → `E_PARSE`. There is no trimming here, use
 *   `parseDate` for user input;
 * - a well-shaped but impossible date (`"2023-02-29"`, `"2024-13-01"`,
 *   `"2024-00-10"`) → `E_RANGE`;
 * - a year before 1000 → `E_RANGE` (years are always 4 digits).
 *
 * Results are produced the same way and must stay in years 1000..9999;
 * a computation that would leave that window fails with `E_RANGE`.
 *
 * ## Weekdays
 *
 * Weekdays are numbered Monday = 0 ... Sunday = 6 (ISO order, NOT the
 * `Date#getDay()` order where Sunday is 0). Where a weekday is passed in or
 * returned as text we use the two-letter RFC 5545 codes `MO TU WE TH FR SA SU`
 * — upper case only; `"mo"` is not a weekday.
 */

const { fail, E_PARSE, E_RANGE, E_TYPE } = require('./errors');

/** Weekday codes, index = weekday number (Monday = 0). */
const WEEKDAYS = Object.freeze(['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']);
const MIN_YEAR = 1000;
const MAX_YEAR = 9999;

const ISO_RE = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/;

/**
 * Mathematical modulo: result has the sign of the divisor.
 * `mod(-1, 7) === 6`, whereas `-1 % 7 === -1`.
 * @param {number} a
 * @param {number} b positive
 * @returns {number}
 */
function mod(a, b) {
  return ((a % b) + b) % b;
}

/** Floor division for integers. */
function div(a, b) {
  return Math.floor(a / b);
}

/**
 * Gregorian leap year rule: divisible by 4, except centuries, except every
 * fourth century.
 * @param {number} y
 */
function isLeap(y) {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

/**
 * Days in month `m` (1..12) of year `y`. No validation.
 * @param {number} y
 * @param {number} m
 */
function dim(y, m) {
  if (m === 2) return isLeap(y) ? 29 : 28;
  return m === 4 || m === 6 || m === 9 || m === 11 ? 30 : 31;
}

/**
 * Civil date → day number (days since 1970-01-01). No validation: callers
 * that accept user values go through {@link makeDays}.
 * @param {number} y
 * @param {number} m 1..12
 * @param {number} d 1..31
 * @returns {number}
 */
function daysFromCivil(y, m, d) {
  y -= m <= 2 ? 1 : 0;
  const era = div(y, 400);
  const yoe = y - era * 400;
  const mp = m > 2 ? m - 3 : m + 9;
  const doy = div(153 * mp + 2, 5) + d - 1;
  const doe = yoe * 365 + div(yoe, 4) - div(yoe, 100) + doy;
  return era * 146097 + doe - 719468;
}

/**
 * Day number → `[year, month, day]`.
 * @param {number} z
 * @returns {[number, number, number]}
 */
function civilFromDays(z) {
  z += 719468;
  const era = div(z, 146097);
  const doe = z - era * 146097;
  const yoe = div(doe - div(doe, 1460) + div(doe, 36524) - div(doe, 146096), 365);
  const y = yoe + era * 400;
  const doy = doe - (365 * yoe + div(yoe, 4) - div(yoe, 100));
  const mp = div(5 * doy + 2, 153);
  const d = doy - div(153 * mp + 2, 5) + 1;
  const m = mp < 10 ? mp + 3 : mp - 9;
  return [m <= 2 ? y + 1 : y, m, d];
}

/**
 * Weekday number of a day number (Monday = 0). Day 0 (1970-01-01) was a
 * Thursday, hence the `+ 3`.
 * @param {number} n
 */
function wd(n) {
  return mod(n + 3, 7);
}

/** True for a JavaScript number that is an integer (booleans are not). */
function isInt(v) {
  return typeof v === 'number' && Number.isInteger(v);
}

/** @throws {ChronoError} E_TYPE unless `v` is an integer */
function needInt(v, what = 'value') {
  if (!isInt(v)) fail(E_TYPE, `${what} must be an integer`);
  return v;
}

/** @throws {ChronoError} E_TYPE unless `v` is a string */
function needStr(v, what = 'value') {
  if (typeof v !== 'string') fail(E_TYPE, `${what} must be a string`);
  return v;
}

/** Month 1..12: E_TYPE for a non-integer, E_RANGE otherwise. */
function needMonth(m) {
  needInt(m, 'month');
  if (m < 1 || m > 12) fail(E_RANGE, 'month out of range');
  return m;
}

/** Year 1000..9999: E_TYPE for a non-integer, E_RANGE otherwise. */
function needYear(y) {
  needInt(y, 'year');
  if (y < MIN_YEAR || y > MAX_YEAR) fail(E_RANGE, 'year out of range');
  return y;
}

/**
 * Validated civil date → day number. The checks run in the order year,
 * month, day; each failure is `E_RANGE`.
 */
function makeDays(y, m, d) {
  if (y < MIN_YEAR || y > MAX_YEAR) fail(E_RANGE, 'year out of range');
  if (m < 1 || m > 12) fail(E_RANGE, 'month out of range');
  if (d < 1 || d > dim(y, m)) fail(E_RANGE, 'day out of range');
  return daysFromCivil(y, m, d);
}

/**
 * Strict `YYYY-MM-DD` → day number (see the module comment for the error
 * rules).
 * @param {string} value
 * @param {string} [what] name used in error messages
 */
function toDays(value, what = 'date') {
  needStr(value, what);
  const mt = ISO_RE.exec(value);
  if (!mt) fail(E_PARSE, `${what} must be YYYY-MM-DD`);
  return makeDays(parseInt(mt[1], 10), parseInt(mt[2], 10), parseInt(mt[3], 10));
}

/** `null`/`undefined` pass through as `null`, anything else goes to toDays. */
function optDays(value, what = 'date') {
  return value == null ? null : toDays(value, what);
}

/** Alias of {@link civilFromDays}, reads better at call sites. */
function ymd(n) {
  return civilFromDays(n);
}

function pad(n, width) {
  return String(n).padStart(width, '0');
}

/**
 * Day number → `"YYYY-MM-DD"`. Results outside years 1000..9999 are refused
 * with `E_RANGE` ("result out of supported range").
 * @param {number} n
 */
function iso(n) {
  const [y, m, d] = civilFromDays(n);
  if (y < MIN_YEAR || y > MAX_YEAR) fail(E_RANGE, 'result out of supported range');
  return `${pad(y, 4)}-${pad(m, 2)}-${pad(d, 2)}`;
}

/**
 * Weekday code → number. E_TYPE for a non-string, E_RANGE for an unknown
 * code (codes are case sensitive).
 * @param {string} code
 */
function weekdayIndex(code, what = 'weekday') {
  needStr(code, what);
  const i = WEEKDAYS.indexOf(code);
  if (i < 0) fail(E_RANGE, `unknown ${what} code`);
  return i;
}

/** @throws E_TYPE unless `v` is an array */
function needList(v, what = 'value') {
  if (!Array.isArray(v)) fail(E_TYPE, `${what} must be an array`);
  return v;
}

/** @throws E_TYPE unless `v` is a plain object (not null, not an array) */
function needDict(v, what = 'value') {
  if (v === null || typeof v !== 'object' || Array.isArray(v)) fail(E_TYPE, `${what} must be an object`);
  return v;
}

/** Numeric ascending comparator for Array#sort. */
function byNumber(a, b) {
  return a - b;
}

/** Unique values of an iterable of numbers, sorted ascending. */
function sortedUnique(values) {
  return Array.from(new Set(values)).sort(byNumber);
}

module.exports = {
  WEEKDAYS,
  MIN_YEAR,
  MAX_YEAR,
  mod,
  div,
  isLeap,
  dim,
  daysFromCivil,
  civilFromDays,
  wd,
  isInt,
  needInt,
  needStr,
  needMonth,
  needYear,
  makeDays,
  toDays,
  optDays,
  ymd,
  pad,
  iso,
  weekdayIndex,
  needList,
  needDict,
  byNumber,
  sortedUnique,
};
