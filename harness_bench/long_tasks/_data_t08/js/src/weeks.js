'use strict';

/**
 * @module weeks
 *
 * Week numbering. Two unrelated systems live here:
 *
 * **ISO 8601 weeks** ({@link isoWeek}, {@link isoWeekStart},
 * {@link isoWeeksInYear}, {@link isoWeekLabel}). Weeks start on Monday and
 * week 1 is the week that contains the first Thursday of the year (equivalently,
 * the week containing 4 January). Consequently the *ISO week-year* differs
 * from the calendar year around New Year:
 *
 *     isoWeek('2021-01-03') // { year: 2020, week: 53 }
 *     isoWeek('2024-12-30') // { year: 2025, week: 1 }
 *
 * Always use the `year` returned together with the week, never the calendar
 * year of the date.
 *
 * **Simple "row" numbering** ({@link weekOfMonth}, {@link weekOfYear},
 * {@link weeksInMonth}) as printed on wall calendars: week 1 is the row that
 * contains the 1st of the month (or 1 January) no matter how few days of it
 * belong to the month, and a new row begins on `weekStart` (Monday by
 * default). A month therefore spans 4 to 6 rows; a year spans 53 or 54.
 * These numbers have nothing to do with ISO weeks.
 */

const { daysFromCivil, dim, iso, mod, div, needInt, needMonth, toDays, wd, weekdayIndex, ymd, pad } = require('./internal');
const { fail, E_RANGE } = require('./errors');

/**
 * Internal: ISO [weekYear, week] of a day number. The Thursday of the same
 * ISO week decides the year.
 * @param {number} n
 * @returns {[number, number]}
 */
function isoWeekOf(n) {
  const thursday = n - wd(n) + 3;
  const [y] = ymd(thursday);
  return [y, div(thursday - daysFromCivil(y, 1, 1), 7) + 1];
}

/**
 * ISO week-year and week number.
 *
 * @param {string} date
 * @returns {{year: number, week: number}}
 * @example isoWeek('2024-03-05') // { year: 2024, week: 10 }
 */
function isoWeek(date) {
  const [year, week] = isoWeekOf(toDays(date));
  return { year, week };
}

/**
 * Number of ISO weeks in an ISO week-year: 52 or 53. A year has 53 weeks when
 * it starts on a Thursday, or is a leap year starting on a Wednesday. We
 * compute it as the week number of 28 December, which is always in the last
 * week.
 *
 * @param {number} year
 * @returns {number}
 */
function isoWeeksInYear(year) {
  needInt(year, 'year');
  return isoWeekOf(daysFromCivil(year, 12, 28))[1];
}

/**
 * Monday of the given ISO week.
 *
 * @param {number} year ISO week-year
 * @param {number} week 1..isoWeeksInYear(year)
 * @returns {string}
 * @throws {ChronoError} E_RANGE when the week does not exist in that year
 * @example isoWeekStart(2020, 53) // '2020-12-28'
 * @example isoWeekStart(2025, 1)  // '2024-12-30'
 */
function isoWeekStart(year, week) {
  needInt(year, 'year');
  needInt(week, 'week');
  if (week < 1 || week > isoWeeksInYear(year)) fail(E_RANGE, 'week out of range');
  const jan4 = daysFromCivil(year, 1, 4);
  return iso(jan4 - wd(jan4) + (week - 1) * 7);
}

/**
 * ISO week label `YYYY-Www` (two-digit week), e.g. `2021-W01`. Uses the
 * week-year, so `isoWeekLabel('2021-01-03') === '2020-W53'`.
 *
 * @param {string} date
 * @returns {string}
 */
function isoWeekLabel(date) {
  const [y, w] = isoWeekOf(toDays(date));
  return `${pad(y, 4)}-W${pad(w, 2)}`;
}

/**
 * Row of the month the date falls into, 1-based, rows starting on
 * `weekStart`.
 *
 * @param {string} date
 * @param {object} [options]
 * @param {string} [options.weekStart='MO']
 * @returns {number} 1..6
 * @example weekOfMonth('2024-09-01')                      // 1 (a Sunday; the row began on Mon 26 Aug)
 * @example weekOfMonth('2024-09-02')                      // 2
 * @example weekOfMonth('2024-09-02', { weekStart: 'SU' }) // 1
 */
function weekOfMonth(date, { weekStart = 'MO' } = {}) {
  const n = toDays(date);
  const ws = weekdayIndex(weekStart, 'weekStart');
  const [y, m, d] = ymd(n);
  const offset = mod(wd(daysFromCivil(y, m, 1)) - ws, 7);
  return div(d - 1 + offset, 7) + 1;
}

/**
 * Row of the year, 1-based: week 1 contains 1 January; rows start on
 * `weekStart`.
 *
 * @param {string} date
 * @param {object} [options]
 * @param {string} [options.weekStart='MO']
 * @returns {number} 1..54
 */
function weekOfYear(date, { weekStart = 'MO' } = {}) {
  const n = toDays(date);
  const ws = weekdayIndex(weekStart, 'weekStart');
  const [y] = ymd(n);
  const jan1 = daysFromCivil(y, 1, 1);
  const offset = mod(wd(jan1) - ws, 7);
  return div(n - jan1 + offset, 7) + 1;
}

/**
 * How many rows the month occupies on a wall calendar (4..6).
 *
 * @param {number} year
 * @param {number} month
 * @param {object} [options]
 * @param {string} [options.weekStart='MO']
 * @returns {number}
 * @example weeksInMonth(2021, 2) // 4 (February 2021 starts on a Monday)
 */
function weeksInMonth(year, month, { weekStart = 'MO' } = {}) {
  needInt(year, 'year');
  needMonth(month);
  const ws = weekdayIndex(weekStart, 'weekStart');
  const offset = mod(wd(daysFromCivil(year, month, 1)) - ws, 7);
  return div(dim(year, month) - 1 + offset, 7) + 1;
}

module.exports = {
  isoWeekOf,
  isoWeek,
  isoWeeksInYear,
  isoWeekStart,
  isoWeekLabel,
  weekOfMonth,
  weekOfYear,
  weeksInMonth,
};
