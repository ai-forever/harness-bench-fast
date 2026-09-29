'use strict';

/**
 * @module business
 *
 * Arithmetic in working days. All functions take an optional calendar spec
 * (see calendar.js); `null` means Saturday/Sunday off, no holidays.
 *
 * ## Conventions — read before use
 *
 * The conventions below were fixed by the payroll service years ago and
 * are relied upon; they are not all "natural":
 *
 * - {@link addBusinessDays} never counts the start date. With `n = 0` it
 *   returns the date itself when it is a working day, otherwise the **next**
 *   working day (even for negative intent — there is no sign on zero).
 * - {@link businessDaysBetween} counts working days in the half-open interval
 *   **(start, end]**: the start is excluded, the end included. It is negative
 *   when `end` is before `start` (then it counts (end, start] and negates).
 *   So `businessDaysBetween(fri, mon) === 1` and
 *   `businessDaysBetween(sat, sun) === 0`.
 * - {@link workHoursBetween} on the other hand is **inclusive on both
 *   ends** — `[start, end]` — and returns 0 (not a negative number) when
 *   `end` is before `start`. It sums {@link workHours}, so preholidays count
 *   with the shortened length.
 *
 * Loops are guarded: more than 100000 steps is `E_LIMIT`.
 */

const { daysFromCivil, dim, iso, needInt, needMonth, needYear, toDays } = require('./internal');
const { buildCal } = require('./calendar');
const { fail, E_LIMIT, E_RANGE } = require('./errors');

const MAX_STEPS = 100000;

/** First working day number at or after n. */
function forward(cal, n) {
  let steps = 0;
  while (!cal.working(n)) {
    n += 1;
    steps += 1;
    if (steps > MAX_STEPS) fail(E_LIMIT, 'no working day found');
  }
  return n;
}

/** Last working day number at or before n. */
function backward(cal, n) {
  let steps = 0;
  while (!cal.working(n)) {
    n -= 1;
    steps += 1;
    if (steps > MAX_STEPS) fail(E_LIMIT, 'no working day found');
  }
  return n;
}

/**
 * Move `n` working days forward (n > 0) or backward (n < 0).
 *
 * The start date is not counted. Every step moves one calendar day and the
 * step counts when the day reached is a working day. Consequently, starting
 * on a Saturday, `+1` is Monday and `-1` is Friday.
 *
 * @param {string} date
 * @param {number} n integer
 * @param {object|null} [calendar]
 * @returns {string}
 * @example addBusinessDays('2024-03-08', 1) // '2024-03-11' (Fri → Mon)
 * @example addBusinessDays('2024-03-09', 0) // '2024-03-11' (Sat → next working day)
 * @example addBusinessDays('2024-03-09', -1) // '2024-03-08'
 */
function addBusinessDays(date, n, calendar = null) {
  let d = toDays(date);
  needInt(n, 'n');
  const cal = buildCal(calendar);
  if (n === 0) return iso(forward(cal, d));
  const step = n > 0 ? 1 : -1;
  let left = Math.abs(n);
  let steps = 0;
  while (left) {
    d += step;
    steps += 1;
    if (steps > MAX_STEPS) fail(E_LIMIT, 'too many steps');
    if (cal.working(d)) left -= 1;
  }
  return iso(d);
}

/**
 * Signed count of working days in (start, end] — see the module comment.
 *
 * @param {string} start
 * @param {string} end
 * @param {object|null} [calendar]
 * @returns {number}
 * @throws {ChronoError} E_LIMIT when the dates are more than 100000 days apart
 * @example businessDaysBetween('2024-03-08', '2024-03-11') // 1
 * @example businessDaysBetween('2024-03-11', '2024-03-08') // -1
 */
function businessDaysBetween(start, end, calendar = null) {
  const a = toDays(start);
  const b = toDays(end);
  const cal = buildCal(calendar);
  if (a === b) return 0;
  const [lo, hi, sign] = a < b ? [a, b, 1] : [b, a, -1];
  if (hi - lo > MAX_STEPS) fail(E_LIMIT, 'range too long');
  let count = 0;
  for (let x = lo + 1; x <= hi; x += 1) if (cal.working(x)) count += 1;
  return sign * count;
}

/**
 * The first working day strictly after `date`.
 * @param {string} date
 * @param {object|null} [calendar]
 * @returns {string}
 */
function nextBusinessDay(date, calendar = null) {
  const d = toDays(date);
  return iso(forward(buildCal(calendar), d + 1));
}

/**
 * The last working day strictly before `date`.
 * @param {string} date
 * @param {object|null} [calendar]
 * @returns {string}
 */
function prevBusinessDay(date, calendar = null) {
  const d = toDays(date);
  return iso(backward(buildCal(calendar), d - 1));
}

/** Working day numbers of a month, ascending. */
function monthWorking(year, month, cal) {
  const first = daysFromCivil(year, month, 1);
  const out = [];
  for (let x = first; x < first + dim(year, month); x += 1) if (cal.working(x)) out.push(x);
  return out;
}

/**
 * The n-th working day of a month (n ≥ 1), or counted from the end (n ≤ -1:
 * -1 is the last working day).
 *
 * @param {number} year 1000..9999
 * @param {number} month
 * @param {number} n non-zero integer
 * @param {object|null} [calendar]
 * @returns {string|null} null when the month has fewer working days
 * @throws {ChronoError} E_RANGE for n = 0
 * @example nthBusinessDay(2024, 1, 1, { holidays: ['2024-01-01', '2024-01-02'] }) // '2024-01-03'
 */
function nthBusinessDay(year, month, n, calendar = null) {
  needYear(year);
  needMonth(month);
  needInt(n, 'n');
  if (n === 0) fail(E_RANGE, 'n must not be zero');
  const days = monthWorking(year, month, buildCal(calendar));
  const idx = n > 0 ? n - 1 : days.length + n;
  if (idx < 0 || idx >= days.length) return null;
  return iso(days[idx]);
}

/**
 * Last working day of the month (`nthBusinessDay(year, month, -1, calendar)`).
 * @returns {string|null}
 */
function lastBusinessDay(year, month, calendar = null) {
  return nthBusinessDay(year, month, -1, calendar);
}

/**
 * Number of working days in a month.
 * @param {number} year
 * @param {number} month
 * @param {object|null} [calendar]
 * @returns {number}
 */
function businessDaysInMonth(year, month, calendar = null) {
  needYear(year);
  needMonth(month);
  return monthWorking(year, month, buildCal(calendar)).length;
}

/**
 * Normative working hours of a month ("норма часов"): the sum of
 * {@link workHours} over all its days.
 *
 * @param {number} year
 * @param {number} month
 * @param {object|null} [calendar]
 * @returns {number}
 */
function workHoursInMonth(year, month, calendar = null) {
  needYear(year);
  needMonth(month);
  const cal = buildCal(calendar);
  const first = daysFromCivil(year, month, 1);
  let total = 0;
  for (let x = first; x < first + dim(year, month); x += 1) total += cal.hours(x);
  return total;
}

/**
 * Sum of working hours over **[start, end] inclusive**; 0 when end < start.
 *
 * @param {string} start
 * @param {string} end
 * @param {object|null} [calendar]
 * @returns {number}
 */
function workHoursBetween(start, end, calendar = null) {
  const a = toDays(start);
  const b = toDays(end);
  const cal = buildCal(calendar);
  if (b < a) return 0;
  if (b - a > MAX_STEPS) fail(E_LIMIT, 'range too long');
  let total = 0;
  for (let x = a; x <= b; x += 1) total += cal.hours(x);
  return total;
}

/**
 * All working days in [start, end] (inclusive), ascending. Empty when end is
 * before start; `E_LIMIT` when the range is longer than 3660 days.
 *
 * @param {string} start
 * @param {string} end
 * @param {object|null} [calendar]
 * @returns {string[]}
 */
function businessDaysList(start, end, calendar = null) {
  const a = toDays(start);
  const b = toDays(end);
  const cal = buildCal(calendar);
  if (b < a) return [];
  if (b - a > 3660) fail(E_LIMIT, 'range too long');
  const out = [];
  for (let x = a; x <= b; x += 1) if (cal.working(x)) out.push(iso(x));
  return out;
}

module.exports = {
  addBusinessDays,
  businessDaysBetween,
  nextBusinessDay,
  prevBusinessDay,
  nthBusinessDay,
  lastBusinessDay,
  businessDaysInMonth,
  workHoursInMonth,
  workHoursBetween,
  businessDaysList,
};
