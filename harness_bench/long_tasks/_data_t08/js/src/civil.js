'use strict';

/**
 * @module civil
 *
 * Plain civil-date arithmetic on `"YYYY-MM-DD"` strings.
 *
 * Every function here takes and returns ISO date strings (see internal.js for
 * the exact validation rules) and never looks at a clock or a time zone.
 *
 * ## Month arithmetic: overflow, not clamping
 *
 * {@link addMonths} deliberately reproduces the behaviour of
 * `Date.prototype.setMonth`, because the first version of the planning
 * service was written on top of it and a lot of stored schedules depend on
 * the result: when the day of month does not exist in the target month, the
 * surplus days **spill over** into the following month.
 *
 *     addMonths('2024-01-31', 1)  === '2024-03-02'   // Feb 2024 has 29 days: 31 - 29 = 2
 *     addMonths('2023-01-31', 1)  === '2023-03-03'   // Feb 2023 has 28 days
 *     addMonths('2024-03-31', -1) === '2024-03-02'   // "one month back" lands after Feb!
 *     addMonths('2024-05-31', 1)  === '2024-07-01'
 *     addYears('2024-02-29', 1)   === '2025-03-01'
 *
 * The overflow is computed as "first day of the target month plus (day - 1)
 * days", so it can never skip more than three days. If you need the more
 * intuitive "end of month" behaviour use {@link addMonthsClamped}.
 *
 * Several other modules build on these two functions; which one they use is
 * stated in their documentation (duration.js: `addDuration` overflows,
 * `diffDates` clamps).
 */

const {
  WEEKDAYS,
  daysFromCivil,
  dim,
  isLeap,
  iso,
  makeDays,
  mod,
  div,
  needInt,
  needList,
  needMonth,
  needStr,
  toDays,
  wd,
  weekdayIndex,
  ymd,
  byNumber,
} = require('./internal');
const { ChronoError, fail, E_LIMIT, E_RANGE } = require('./errors');

/** Upper bound on the number of dates returned by {@link eachDay}. */
const MAX_RANGE_ITEMS = 3660;

/**
 * Gregorian leap-year test.
 *
 * @param {number} year any integer (no range check here)
 * @returns {boolean}
 * @throws {ChronoError} E_TYPE when `year` is not an integer
 * @example isLeapYear(2000) // true
 * @example isLeapYear(1900) // false
 */
function isLeapYear(year) {
  needInt(year, 'year');
  return isLeap(year);
}

/**
 * Number of days in a month.
 *
 * @param {number} year any integer
 * @param {number} month 1..12
 * @returns {number} 28..31
 * @throws {ChronoError} E_TYPE for non-integers, E_RANGE for a month outside 1..12
 * @example daysInMonth(2024, 2) // 29
 */
function daysInMonth(year, month) {
  needInt(year, 'year');
  needMonth(month);
  return dim(year, month);
}

/**
 * 365 or 366.
 * @param {number} year
 * @returns {number}
 */
function daysInYear(year) {
  needInt(year, 'year');
  return isLeap(year) ? 366 : 365;
}

/**
 * Validation without exceptions: `true` when `value` is a valid strict ISO
 * date that chronorule would accept, `false` for anything else — including
 * non-strings, which would normally be an `E_TYPE`. Never throws.
 *
 * @param {*} value
 * @returns {boolean}
 * @example isValidDate('2024-02-29') // true
 * @example isValidDate('2023-02-29') // false
 * @example isValidDate(20240229)     // false
 */
function isValidDate(value) {
  try {
    toDays(value);
  } catch (err) {
    if (err instanceof ChronoError) return false;
    throw err;
  }
  return true;
}

/**
 * Build a date string from numeric parts, validating them.
 *
 * @param {number} year 1000..9999
 * @param {number} month 1..12
 * @param {number} day 1..daysInMonth
 * @returns {string} `"YYYY-MM-DD"`
 * @throws {ChronoError} E_TYPE for non-integers; E_RANGE for impossible dates
 *   (checked in the order year, month, day)
 */
function makeDate(year, month, day) {
  needInt(year, 'year');
  needInt(month, 'month');
  needInt(day, 'day');
  return iso(makeDays(year, month, day));
}

/**
 * Split a date into numbers.
 * @param {string} date
 * @returns {{year: number, month: number, day: number}}
 */
function dateParts(date) {
  const [year, month, day] = ymd(toDays(date));
  return { year, month, day };
}

/**
 * Weekday number, **Monday = 0 ... Sunday = 6**.
 *
 * Careful when porting code that used `Date#getDay()`: there Sunday is 0.
 *
 * @param {string} date
 * @returns {number}
 * @example weekday('2024-03-04') // 0 (a Monday)
 * @example weekday('2024-03-10') // 6 (a Sunday)
 */
function weekday(date) {
  return wd(toDays(date));
}

/**
 * Weekday as a two-letter code (`"MO"` ... `"SU"`).
 * @param {string} date
 * @returns {string}
 */
function weekdayCode(date) {
  return WEEKDAYS[wd(toDays(date))];
}

/**
 * Ordinal day of the year, 1-based (1 January = 1, 31 December = 365/366).
 * @param {string} date
 * @returns {number}
 */
function dayOfYear(date) {
  const n = toDays(date);
  const [y] = ymd(n);
  return n - daysFromCivil(y, 1, 1) + 1;
}

/**
 * Add (or subtract, for negative `n`) whole days.
 *
 * @param {string} date
 * @param {number} n integer, may be negative
 * @returns {string}
 * @throws {ChronoError} E_RANGE when the result leaves years 1000..9999
 */
function addDays(date, n) {
  const d = toDays(date);
  needInt(n, 'n');
  return iso(d + n);
}

/** [year, month] shifted by n months. */
function shiftMonth(y, m, n) {
  const total = y * 12 + (m - 1) + n;
  const ny = div(total, 12);
  return [ny, total - ny * 12 + 1];
}

/**
 * Add calendar months with **overflow** semantics (see the module comment).
 *
 * The year and month are shifted first; if the original day does not exist
 * in the target month, the result is `firstOfTargetMonth + (day - 1)` days,
 * i.e. it spills into the next month.
 *
 * @param {string} date
 * @param {number} n months, may be negative or zero
 * @returns {string}
 * @throws {ChronoError} E_RANGE when the target year leaves 1000..9999
 * @example addMonths('2024-01-31', 1)  // '2024-03-02'
 * @example addMonths('2024-01-30', 1)  // '2024-03-01'
 * @example addMonths('2024-01-29', 1)  // '2024-02-29'
 * @example addMonths('2024-12-31', -1) // '2024-12-01'  (November has 30 days)
 */
function addMonths(date, n) {
  const [y, m, d] = ymd(toDays(date));
  needInt(n, 'n');
  const [ny, nm] = shiftMonth(y, m, n);
  if (ny < 1000 || ny > 9999) fail(E_RANGE, 'result out of supported range');
  const last = dim(ny, nm);
  if (d <= last) return iso(daysFromCivil(ny, nm, d));
  // Overflow like Date.prototype.setMonth: surplus days spill into the next month.
  return iso(daysFromCivil(ny, nm, 1) + d - 1);
}

/**
 * Add calendar months, clamping the day to the last day of the target month.
 *
 * @param {string} date
 * @param {number} n
 * @returns {string}
 * @example addMonthsClamped('2024-01-31', 1) // '2024-02-29'
 * @example addMonthsClamped('2024-03-31', -1) // '2024-02-29'
 */
function addMonthsClamped(date, n) {
  const [y, m, d] = ymd(toDays(date));
  needInt(n, 'n');
  const [ny, nm] = shiftMonth(y, m, n);
  if (ny < 1000 || ny > 9999) fail(E_RANGE, 'result out of supported range');
  return iso(daysFromCivil(ny, nm, Math.min(d, dim(ny, nm))));
}

/**
 * Add whole years. Implemented as `addMonths(date, 12 * n)`, therefore it
 * inherits the overflow: 29 February plus one year is **1 March**.
 *
 * @param {string} date
 * @param {number} n
 * @returns {string}
 * @example addYears('2024-02-29', 1) // '2025-03-01'
 * @example addYears('2024-02-29', 4) // '2028-02-29'
 */
function addYears(date, n) {
  needInt(n, 'n');
  return addMonths(date, 12 * n);
}

/**
 * Signed number of days from `a` to `b` (`b - a`): positive when `b` is later.
 *
 * @param {string} a
 * @param {string} b
 * @returns {number}
 * @example diffDays('2024-03-01', '2024-02-28') // -2
 */
function diffDays(a, b) {
  return toDays(b) - toDays(a);
}

/**
 * Three-way comparison for sorting: -1, 0 or 1.
 * @param {string} a
 * @param {string} b
 * @returns {number}
 */
function compareDates(a, b) {
  const x = toDays(a);
  const y = toDays(b);
  return (x > y ? 1 : 0) - (x < y ? 1 : 0);
}

/** First day of the month of `date`. */
function startOfMonth(date) {
  const [y, m] = ymd(toDays(date));
  return iso(daysFromCivil(y, m, 1));
}

/** Last day of the month of `date`. */
function endOfMonth(date) {
  const [y, m] = ymd(toDays(date));
  return iso(daysFromCivil(y, m, dim(y, m)));
}

/** First day of the calendar quarter (Jan/Apr/Jul/Oct 1st). */
function startOfQuarter(date) {
  const [y, m] = ymd(toDays(date));
  return iso(daysFromCivil(y, div(m - 1, 3) * 3 + 1, 1));
}

/** Last day of the calendar quarter (Mar 31, Jun 30, Sep 30, Dec 31). */
function endOfQuarter(date) {
  const [y, m] = ymd(toDays(date));
  const qm = div(m - 1, 3) * 3 + 3;
  return iso(daysFromCivil(y, qm, dim(y, qm)));
}

/** 1 January of the year of `date`. */
function startOfYear(date) {
  const [y] = ymd(toDays(date));
  return iso(daysFromCivil(y, 1, 1));
}

/** 31 December of the year of `date`. */
function endOfYear(date) {
  const [y] = ymd(toDays(date));
  return iso(daysFromCivil(y, 12, 31));
}

/**
 * First day of the week containing `date`. Weeks start on Monday unless
 * `options.weekStart` names another weekday.
 *
 * @param {string} date
 * @param {object} [options]
 * @param {string} [options.weekStart='MO'] weekday code the week starts on
 * @returns {string}
 * @example startOfWeek('2024-03-06')                      // '2024-03-04'
 * @example startOfWeek('2024-03-06', { weekStart: 'SU' }) // '2024-03-03'
 */
function startOfWeek(date, { weekStart = 'MO' } = {}) {
  const n = toDays(date);
  const ws = weekdayIndex(weekStart, 'weekStart');
  return iso(n - mod(wd(n) - ws, 7));
}

/**
 * Last day of the week containing `date` (six days after {@link startOfWeek}).
 *
 * @param {string} date
 * @param {object} [options]
 * @param {string} [options.weekStart='MO']
 * @returns {string}
 */
function endOfWeek(date, { weekStart = 'MO' } = {}) {
  const n = toDays(date);
  const ws = weekdayIndex(weekStart, 'weekStart');
  return iso(n - mod(wd(n) - ws, 7) + 6);
}

/**
 * Clamp a date into `[lo, hi]`.
 *
 * @param {string} date
 * @param {string} lo
 * @param {string} hi
 * @returns {string}
 * @throws {ChronoError} E_RANGE when `lo` is after `hi`
 */
function clampDate(date, lo, hi) {
  const n = toDays(date);
  const a = toDays(lo);
  const b = toDays(hi);
  if (a > b) fail(E_RANGE, 'lower bound after upper bound');
  return iso(Math.min(Math.max(n, a), b));
}

/**
 * Earliest date of a non-empty array.
 * @param {string[]} dates
 * @returns {string}
 * @throws {ChronoError} E_TYPE when not an array, E_RANGE when empty
 */
function minDate(dates) {
  needList(dates, 'dates');
  if (dates.length === 0) fail(E_RANGE, 'empty list');
  return iso(Math.min(...dates.map((x) => toDays(x))));
}

/**
 * Latest date of a non-empty array.
 * @param {string[]} dates
 * @returns {string}
 */
function maxDate(dates) {
  needList(dates, 'dates');
  if (dates.length === 0) fail(E_RANGE, 'empty list');
  return iso(Math.max(...dates.map((x) => toDays(x))));
}

/**
 * Sort dates chronologically. Returns a new array; the input is not modified.
 *
 * @param {string[]} dates
 * @param {object} [options]
 * @param {boolean} [options.descending=false] latest first
 * @param {boolean} [options.unique=false] drop duplicates
 * @returns {string[]}
 */
function sortDates(dates, { descending = false, unique = false } = {}) {
  needList(dates, 'dates');
  let nums = dates.map((x) => toDays(x));
  if (unique) nums = Array.from(new Set(nums));
  nums.sort(descending ? (a, b) => b - a : byNumber);
  return nums.map(iso);
}

/**
 * All dates from `start` to `end`, both inclusive, every `step` days.
 *
 * Returns an empty array when `end` is before `start` (the range is not
 * reversed). Refuses to produce more than 3660 dates (`E_LIMIT`), roughly ten
 * years of days.
 *
 * @param {string} start
 * @param {string} end
 * @param {object} [options]
 * @param {number} [options.step=1] positive integer
 * @returns {string[]}
 * @throws {ChronoError} E_RANGE for step < 1, E_LIMIT for too many dates
 * @example eachDay('2024-02-27', '2024-03-02', { step: 2 }) // ['2024-02-27', '2024-02-29', '2024-03-02']
 */
function eachDay(start, end, { step = 1 } = {}) {
  const a = toDays(start);
  const b = toDays(end);
  needInt(step, 'step');
  if (step < 1) fail(E_RANGE, 'step must be positive');
  if (b < a) return [];
  if (div(b - a, step) + 1 > MAX_RANGE_ITEMS) fail(E_LIMIT, 'too many days');
  const out = [];
  for (let x = a; x <= b; x += step) out.push(iso(x));
  return out;
}

/**
 * Same calendar month and year.
 * @param {string} a
 * @param {string} b
 * @returns {boolean}
 */
function isSameMonth(a, b) {
  const [ya, ma] = ymd(toDays(a));
  const [yb, mb] = ymd(toDays(b));
  return ya === yb && ma === mb;
}

/**
 * Is the date a weekend day? Only looks at the weekday; holidays are the
 * business of calendar.js.
 *
 * @param {string} date
 * @param {object} [options]
 * @param {string[]} [options.weekend=['SA','SU']] weekday codes that count as weekend
 * @returns {boolean}
 */
function isWeekend(date, { weekend = null } = {}) {
  const n = toDays(date);
  if (weekend == null) weekend = ['SA', 'SU'];
  needList(weekend, 'weekend');
  return weekend.map((c) => weekdayIndex(c)).includes(wd(n));
}

/**
 * Compact `YYYYMMDD` form as used in RRULE UNTIL values and file names.
 * @param {string} date
 * @returns {string}
 * @example toCompact('2024-03-05') // '20240305'
 */
function toCompact(date) {
  needStr(date, 'date');
  return iso(toDays(date)).replace(/-/g, '');
}

module.exports = {
  isLeapYear,
  daysInMonth,
  daysInYear,
  isValidDate,
  makeDate,
  dateParts,
  weekday,
  weekdayCode,
  dayOfYear,
  addDays,
  addMonths,
  addMonthsClamped,
  addYears,
  diffDays,
  compareDates,
  startOfMonth,
  endOfMonth,
  startOfQuarter,
  endOfQuarter,
  startOfYear,
  endOfYear,
  startOfWeek,
  endOfWeek,
  clampDate,
  minDate,
  maxDate,
  sortDates,
  eachDay,
  isSameMonth,
  isWeekend,
  toCompact,
};
