'use strict';

/**
 * @module nth
 *
 * "The second Tuesday of the month", "the last Friday", "the next Monday".
 *
 * Weekdays are passed as two-letter codes (`'MO'` ... `'SU'`). The ordinal
 * `n` counts occurrences of that weekday inside the month: `1` is the first,
 * `-1` the last, `-2` the one before the last. Only `1..5` and `-5..-1` are
 * meaningful; `0` or anything beyond ±5 is rejected with `E_RANGE` instead of
 * silently returning `null`.
 *
 * A fifth occurrence does not exist in every month. In that case the
 * functions return `null` — they do **not** roll into the next month.
 *
 *     nthWeekdayOfMonth(2024, 2, 'TH', 5)  // '2024-02-29'
 *     nthWeekdayOfMonth(2024, 2, 'FR', 5)  // null
 *     nthWeekdayOfMonth(2024, 2, 'FR', -5) // null
 */

const { WEEKDAYS, daysFromCivil, dim, iso, mod, div, needInt, needMonth, needYear, toDays, wd, weekdayIndex, ymd } = require('./internal');
const { fail, E_RANGE } = require('./errors');

/**
 * Internal: day of month of the n-th weekday `w` (number), or null.
 * @param {number} year
 * @param {number} month
 * @param {number} w weekday number
 * @param {number} n non-zero ordinal
 * @returns {number|null}
 */
function nthDay(year, month, w, n) {
  const last = dim(year, month);
  if (n > 0) {
    const first = daysFromCivil(year, month, 1);
    const day = 1 + mod(w - wd(first), 7) + (n - 1) * 7;
    return day <= last ? day : null;
  }
  const end = daysFromCivil(year, month, last);
  const day = last - mod(wd(end) - w, 7) - (-n - 1) * 7;
  return day >= 1 ? day : null;
}

/**
 * The n-th given weekday of a month.
 *
 * Argument checks, in order: year (integer 1000..9999), month (1..12),
 * weekday code, `n` (integer, non-zero, |n| ≤ 5).
 *
 * @param {number} year
 * @param {number} month
 * @param {string} weekday code such as `'FR'`
 * @param {number} n 1..5 from the start, -1..-5 from the end
 * @returns {string|null} the date, or `null` when the month has fewer occurrences
 * @throws {ChronoError} E_TYPE / E_RANGE as described above
 * @example nthWeekdayOfMonth(2024, 3, 'FR', -1) // '2024-03-29'
 * @example nthWeekdayOfMonth(2024, 3, 'MO', 1)  // '2024-03-04'
 */
function nthWeekdayOfMonth(year, month, weekday, n) {
  needYear(year);
  needMonth(month);
  const w = weekdayIndex(weekday);
  needInt(n, 'n');
  if (n === 0 || n > 5 || n < -5) fail(E_RANGE, 'n must be 1..5 or -1..-5');
  const day = nthDay(year, month, w, n);
  return day === null ? null : iso(daysFromCivil(year, month, day));
}

/**
 * Shortcut for `nthWeekdayOfMonth(year, month, weekday, -1)`. Never null:
 * every weekday occurs at least four times in a month.
 *
 * @param {number} year
 * @param {number} month
 * @param {string} weekday
 * @returns {string}
 * @example lastWeekdayOfMonth(2024, 5, 'FR') // '2024-05-31'
 */
function lastWeekdayOfMonth(year, month, weekday) {
  return nthWeekdayOfMonth(year, month, weekday, -1);
}

/**
 * Which occurrence of its weekday a date is, counted from both ends of the
 * month.
 *
 * `n` is 1..5 (from the start); `fromEnd` is -1..-5 (from the end, `-1`
 * meaning "the last one"). `weekday` is the code of the date's weekday.
 *
 * @param {string} date
 * @returns {{n: number, fromEnd: number, weekday: string}}
 * @example weekdayOccurrence('2024-03-29') // { n: 5, fromEnd: -1, weekday: 'FR' }
 * @example weekdayOccurrence('2024-03-22') // { n: 4, fromEnd: -2, weekday: 'FR' }
 */
function weekdayOccurrence(date) {
  const n = toDays(date);
  const [y, m, d] = ymd(n);
  return { n: div(d - 1, 7) + 1, fromEnd: -(div(dim(y, m) - d, 7) + 1), weekday: WEEKDAYS[wd(n)] };
}

/**
 * The next date that falls on `weekday`.
 *
 * By default the search is strictly after `date`: asking for the next Monday
 * on a Monday gives the Monday one week later. With `{ inclusive: true }` the
 * date itself is returned when it already matches.
 *
 * @param {string} date
 * @param {string} weekday
 * @param {object} [options]
 * @param {boolean} [options.inclusive=false]
 * @returns {string}
 * @example nextWeekday('2024-03-04', 'MO')                      // '2024-03-11'
 * @example nextWeekday('2024-03-04', 'MO', { inclusive: true }) // '2024-03-04'
 */
function nextWeekday(date, weekday, { inclusive = false } = {}) {
  const n = toDays(date);
  const w = weekdayIndex(weekday);
  let delta = mod(w - wd(n), 7);
  if (delta === 0 && !inclusive) delta = 7;
  return iso(n + delta);
}

/**
 * The previous date that falls on `weekday` (mirror of {@link nextWeekday}).
 *
 * @param {string} date
 * @param {string} weekday
 * @param {object} [options]
 * @param {boolean} [options.inclusive=false]
 * @returns {string}
 */
function prevWeekday(date, weekday, { inclusive = false } = {}) {
  const n = toDays(date);
  const w = weekdayIndex(weekday);
  let delta = mod(wd(n) - w, 7);
  if (delta === 0 && !inclusive) delta = 7;
  return iso(n - delta);
}

/**
 * Every date in the month that falls on `weekday`, ascending (4 or 5 dates).
 *
 * @param {number} year
 * @param {number} month
 * @param {string} weekday
 * @returns {string[]}
 */
function weekdaysInMonth(year, month, weekday) {
  needYear(year);
  needMonth(month);
  const w = weekdayIndex(weekday);
  const first = daysFromCivil(year, month, 1);
  const out = [];
  for (let day = 1 + mod(w - wd(first), 7); day <= dim(year, month); day += 7) {
    out.push(iso(first + day - 1));
  }
  return out;
}

/**
 * True when no later date in the same month has the same weekday, i.e. the
 * date is "the last Xday of the month".
 *
 * @param {string} date
 * @returns {boolean}
 */
function isLastWeekdayOfMonth(date) {
  const [y, m, d] = ymd(toDays(date));
  return d + 7 > dim(y, m);
}

module.exports = {
  nthDay,
  nthWeekdayOfMonth,
  lastWeekdayOfMonth,
  weekdayOccurrence,
  nextWeekday,
  prevWeekday,
  weekdaysInMonth,
  isLastWeekdayOfMonth,
};
