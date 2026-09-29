'use strict';

/**
 * @module schedule
 *
 * A schedule is what the planning UI stores for a recurring payment or
 * report: a recurrence rule plus extra dates, excluded dates and a
 * business-day convention.
 *
 * ```js
 * {
 *   rule: 'FREQ=MONTHLY;BYMONTHDAY=25',   // text or rule object (rrule.js), required
 *   start: '2024-01-01',                   // dtstart of the rule, required
 *   exdates: ['2024-05-25'],               // excluded dates (optional)
 *   rdates: ['2024-12-28'],                // additional dates (optional)
 *   roll: 'following',                     // convention from roll.js, default 'none'
 *   calendar: { holidays: [...] },         // calendar spec, default null (Sat/Sun off)
 * }
 * ```
 *
 * Unknown keys are `E_TYPE`; a missing (or null) `rule` or `start` is
 * `E_TYPE` as well.
 *
 * ## Pipeline
 *
 * 1. The rule is expanded from `start` (COUNT and UNTIL apply to the rule's
 *    own occurrences only).
 * 2. RDATEs **before `start` are ignored**; the others are merged in date
 *    order. A date produced by both the rule and an RDATE appears once.
 * 3. EXDATEs are removed. **EXDATEs are compared with the raw dates, before
 *    rolling**: to cancel a payment that was rolled from Sunday the 25th to
 *    Monday the 26th you have to exclude the 25th. Excluding does not "give
 *    back" a COUNT slot: `COUNT=5` with one excluded occurrence produces 4
 *    dates.
 * 4. Each remaining date is rolled with the convention and calendar.
 * 5. Rolled dates that were already produced are dropped (first one wins).
 *    The order of step 2 is kept; the output is not re-sorted.
 *
 * ## Windows
 *
 * {@link scheduleBetween} expands raw dates only **up to `end`** and keeps
 * the rolled dates that fall into `[start, end]`. A raw date after `end`
 * that would roll back into the window (e.g. `preceding` from a Saturday
 * right after the window) is therefore *not* included — a known limitation
 * kept for compatibility with stored reports. Likewise a raw date inside the
 * window may roll out of it and disappear.
 */

const { iso, needDict, needInt, needList, toDays, byNumber } = require('./internal');
const { buildCal } = require('./calendar');
const { fail, E_RANGE, E_TYPE } = require('./errors');
const { needConvention, rollDays } = require('./roll');
const { iterate, toRule } = require('./rrule');

const SPEC_KEYS = Object.freeze(['rule', 'start', 'exdates', 'rdates', 'roll', 'calendar']);

/**
 * Validate a schedule spec. Checks run in the order: object, unknown keys,
 * rule/start presence, rule, start, exdates, rdates, roll, calendar.
 * @param {object} spec
 */
function buildSchedule(spec) {
  needDict(spec, 'schedule');
  for (const key of Object.keys(spec)) {
    if (!SPEC_KEYS.includes(key)) fail(E_TYPE, 'unknown schedule key');
  }
  if (spec.rule == null || spec.start == null) fail(E_TYPE, 'schedule needs rule and start');
  const s = {};
  s.rule = toRule(spec.rule);
  s.start = toDays(spec.start, 'start');
  const ex = spec.exdates == null ? [] : needList(spec.exdates, 'exdates');
  s.exdates = new Set(ex.map((x) => toDays(x, 'exdate')));
  const rd = spec.rdates == null ? [] : needList(spec.rdates, 'rdates');
  const rdNums = rd.map((x) => toDays(x, 'rdate'));
  s.rdates = Array.from(new Set(rdNums.filter((x) => x >= s.start))).sort(byNumber);
  s.roll = spec.roll == null ? 'none' : needConvention(spec.roll);
  s.cal = buildCal(spec.calendar);
  return s;
}

/** Raw dates: rule occurrences merged with RDATEs, without EXDATEs. */
function* raw(s, stop = null) {
  const extra = s.rdates.filter((x) => stop === null || x <= stop);
  let i = 0;
  let last = null;
  for (const c of iterate(s.rule, s.start, stop)) {
    if (stop !== null && c > stop) break;
    while (i < extra.length && extra[i] <= c) {
      const x = extra[i];
      i += 1;
      if (x !== last && !s.exdates.has(x)) {
        last = x;
        yield x;
      }
    }
    if (c !== last && !s.exdates.has(c)) {
      last = c;
      yield c;
    }
  }
  while (i < extra.length) {
    const x = extra[i];
    i += 1;
    if (x !== last && !s.exdates.has(x)) {
      last = x;
      yield x;
    }
  }
}

/** Rolled and de-duplicated dates. */
function* rolled(s, stop = null) {
  const seen = new Set();
  for (const x of raw(s, stop)) {
    const y = rollDays(s.cal, x, s.roll);
    if (seen.has(y)) continue;
    seen.add(y);
    yield y;
  }
}

/**
 * Schedule dates within `[start, end]` (see "Windows" in the module comment).
 *
 * @param {object} spec
 * @param {string} start
 * @param {string} end
 * @returns {string[]} empty when end < start
 * @example scheduleBetween({ rule: 'FREQ=MONTHLY;BYMONTHDAY=31', start: '2024-01-01',
 *   roll: 'modified_following' }, '2024-08-01', '2024-09-30') // ['2024-08-30']
 */
function scheduleBetween(spec, start, end) {
  const s = buildSchedule(spec);
  const lo = toDays(start, 'start');
  const hi = toDays(end, 'end');
  if (hi < lo) return [];
  const out = [];
  for (const y of rolled(s, hi)) if (lo <= y && y <= hi) out.push(iso(y));
  return out;
}

/**
 * The first `n` schedule dates that are on or after `from` (default: the
 * schedule start), in pipeline order.
 *
 * @param {object} spec
 * @param {number} n positive integer
 * @param {object} [options]
 * @param {string|null} [options.from=null]
 * @returns {string[]} fewer than n when the schedule ends earlier
 */
function scheduleTake(spec, n, { from = null } = {}) {
  const s = buildSchedule(spec);
  needInt(n, 'n');
  if (n < 1) fail(E_RANGE, 'n must be positive');
  const lo = from == null ? s.start : toDays(from, 'from');
  const out = [];
  for (const y of rolled(s)) {
    if (y < lo) continue;
    out.push(iso(y));
    if (out.length >= n) break;
  }
  return out;
}

/**
 * The first schedule date (in pipeline order) strictly after `after`, or
 * null.
 *
 * @param {object} spec
 * @param {string} after
 * @returns {string|null}
 */
function scheduleNext(spec, after) {
  const s = buildSchedule(spec);
  const lo = toDays(after, 'after');
  for (const y of rolled(s)) if (y > lo) return iso(y);
  return null;
}

module.exports = { buildSchedule, scheduleBetween, scheduleTake, scheduleNext };
