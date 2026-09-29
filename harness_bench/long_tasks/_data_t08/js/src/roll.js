'use strict';

/**
 * @module roll
 *
 * Business-day conventions: what to do with a date that is not a working
 * day (a payment date falling on a Sunday, a report due on a holiday).
 *
 * | convention             | non-working date moves to                                          |
 * |------------------------|---------------------------------------------------------------------|
 * | `none`                 | stays where it is                                                   |
 * | `following`            | the next working day                                                |
 * | `preceding`            | the previous working day                                            |
 * | `modified_following`   | the next working day, unless that is in another calendar month — then the previous one |
 * | `modified_preceding`   | the previous working day, unless that is in another calendar month — then the next one |
 * | `nearest`              | the closest working day; on a tie the **later** one wins            |
 *
 * Working days never move. Convention names are plain strings with
 * underscores (they are stored in the database and are not "camelCased" by
 * the ports).
 *
 * `nearest` looks at distance 1 after, then 1 before, then 2 after, 2 before
 * and so on. With the default calendar a Saturday therefore goes to Friday
 * (Sunday, at distance 1 after, is not a working day, Friday at distance 1
 * before is) and a Sunday goes to Monday.
 *
 * The "modified" conventions compare the month of the rolled date with the
 * month of the original date only; a year change is a month change as well.
 *
 * Searches stop after 366 days with `E_LIMIT`.
 */

const { iso, needList, needStr, toDays, ymd } = require('./internal');
const { buildCal } = require('./calendar');
const { fail, E_LIMIT, E_RANGE } = require('./errors');

const CONVENTIONS = Object.freeze([
  'none',
  'following',
  'preceding',
  'modified_following',
  'modified_preceding',
  'nearest',
]);
const MAX_SEARCH = 366;

/**
 * @param {*} convention
 * @returns {string}
 * @throws {ChronoError} E_TYPE for a non-string, E_RANGE for an unknown name
 */
function needConvention(convention) {
  needStr(convention, 'convention');
  if (!CONVENTIONS.includes(convention)) fail(E_RANGE, 'unknown convention');
  return convention;
}

/** Walk from n in `direction` (+1/-1) to the first working day, n included. */
function step(cal, n, direction) {
  for (let i = 0; i < MAX_SEARCH; i += 1) {
    if (cal.working(n)) return n;
    n += direction;
  }
  return fail(E_LIMIT, 'no working day nearby');
}

/**
 * Internal: roll a day number with an already built calendar.
 * @param {import('./calendar').Cal} cal
 * @param {number} n
 * @param {string} convention validated name
 * @returns {number}
 */
function rollDays(cal, n, convention) {
  if (convention === 'none' || cal.working(n)) return n;
  if (convention === 'following') return step(cal, n, 1);
  if (convention === 'preceding') return step(cal, n, -1);
  const month = ymd(n)[1];
  if (convention === 'modified_following') {
    const f = step(cal, n, 1);
    return ymd(f)[1] === month ? f : step(cal, n, -1);
  }
  if (convention === 'modified_preceding') {
    const p = step(cal, n, -1);
    return ymd(p)[1] === month ? p : step(cal, n, 1);
  }
  for (let k = 1; k < MAX_SEARCH; k += 1) {
    if (cal.working(n + k)) return n + k;
    if (cal.working(n - k)) return n - k;
  }
  return fail(E_LIMIT, 'no working day nearby');
}

/**
 * Apply a business-day convention to one date.
 *
 * Checks run in the order: date, convention, calendar.
 *
 * @param {string} date
 * @param {string} convention one of the names in the table above
 * @param {object|null} [calendar]
 * @returns {string}
 * @example roll('2024-03-31', 'following')          // '2024-04-01'
 * @example roll('2024-03-31', 'modified_following') // '2024-03-29'
 * @example roll('2024-06-01', 'modified_preceding') // '2024-06-03'
 */
function roll(date, convention, calendar = null) {
  const n = toDays(date);
  needConvention(convention);
  const cal = buildCal(calendar);
  return iso(rollDays(cal, n, convention));
}

/**
 * Roll many dates. The output keeps the input order. With `dedupe` (the
 * default) a rolled date that was already produced is skipped — the
 * **first** input that rolled onto it wins; the output is not re-sorted.
 *
 * @param {string[]} dates
 * @param {string} convention
 * @param {object|null} [calendar]
 * @param {object} [options]
 * @param {boolean} [options.dedupe=true]
 * @returns {string[]}
 */
function rollMany(dates, convention, calendar = null, { dedupe = true } = {}) {
  needList(dates, 'dates');
  const nums = dates.map((x) => toDays(x));
  needConvention(convention);
  const cal = buildCal(calendar);
  const out = [];
  const seen = new Set();
  for (const n of nums) {
    const r = rollDays(cal, n, convention);
    if (dedupe && seen.has(r)) continue;
    seen.add(r);
    out.push(iso(r));
  }
  return out;
}

/**
 * Would the convention move this date?
 * @param {string} date
 * @param {string} convention
 * @param {object|null} [calendar]
 * @returns {boolean}
 */
function isRolled(date, convention, calendar = null) {
  return roll(date, convention, calendar) !== date;
}

module.exports = { CONVENTIONS, needConvention, rollDays, roll, rollMany, isRolled };
