'use strict';

/**
 * @module calendar
 *
 * Production calendars ("производственный календарь"): which days are worked
 * and how many hours.
 *
 * ## Calendar specification
 *
 * Functions that need a calendar take a plain object (a *spec*). `null` or
 * `undefined` means the default calendar: Saturday and Sunday off, no
 * holidays, 8-hour days.
 *
 * ```js
 * {
 *   weekend:  ['SA', 'SU'],          // weekday codes that are days off
 *   holidays: ['2024-01-01', ...],   // public holidays (days off)
 *   workdays: ['2024-04-27', ...],   // "transferred" working days, usually Saturdays
 *   dayHours: 8,                     // length of a normal working day, 1..24
 *   preholidayCut: 1,                // hours cut on the day before a holiday, 0..dayHours-1
 * }
 * ```
 *
 * Every key is optional; `null` for a key means "use the default". Any other
 * key is an error (`E_TYPE`) — this catches typos such as `holiday:`.
 * Validation runs in this order: unknown keys, `weekend`, `holidays`,
 * `workdays`, `dayHours`, `preholidayCut`.
 *
 * - `weekend` may be empty, may contain duplicates, but must leave at least
 *   one working weekday: seven distinct codes is `E_RANGE`.
 * - `holidays` and `workdays` are arrays of strict ISO dates.
 * - `dayHours` must be an integer 1..24; `preholidayCut` an integer with
 *   `0 <= preholidayCut < dayHours` (both `E_RANGE` otherwise).
 *
 * ## Precedence
 *
 * The status of a day is decided in this order:
 *
 * 1. listed in `holidays` → **holiday** (a holiday wins even if the same
 *    date is also listed in `workdays`; {@link makeCalendar} drops such dates
 *    from `workdays`);
 * 2. not listed in `workdays` and its weekday is in `weekend` → **weekend**;
 * 3. otherwise it is a working day, and
 *    - if the *next calendar day* is a listed holiday → **preholiday**
 *      (shortened by `preholidayCut` hours). Only listed holidays count: the
 *      Friday before an ordinary weekend is not shortened;
 *    - else if the date is listed in `workdays` → **transfer** (even when the
 *      listed date is an ordinary weekday — the list is taken literally);
 *    - else → **workday**.
 *
 * Holidays that fall on a weekend are *not* moved automatically: the
 * government decree lists the transfers explicitly and so must the spec.
 */

const { WEEKDAYS, iso, needDict, needInt, needList, toDays, wd, weekdayIndex, byNumber } = require('./internal');
const { fail, E_RANGE, E_TYPE } = require('./errors');

const CAL_KEYS = Object.freeze(['weekend', 'holidays', 'workdays', 'dayHours', 'preholidayCut']);
const DEFAULT_WEEKEND = Object.freeze(['SA', 'SU']);

/**
 * Normalised calendar with fast lookups. Built by {@link buildCal}; not part
 * of the public API.
 */
class Cal {
  constructor() {
    /** @type {Set<number>} weekday numbers */
    this.weekend = new Set();
    /** @type {Set<number>} day numbers */
    this.holidays = new Set();
    /** @type {Set<number>} day numbers */
    this.workdays = new Set();
    this.dayHours = 8;
    this.cut = 1;
  }

  /** Is day number `n` a working day (workday, transfer or preholiday)? */
  working(n) {
    if (this.holidays.has(n)) return false;
    return this.workdays.has(n) || !this.weekend.has(wd(n));
  }

  /** Kind of day number `n`; see the module comment for the precedence. */
  kind(n) {
    if (this.holidays.has(n)) return 'holiday';
    if (!(this.workdays.has(n) || !this.weekend.has(wd(n)))) return 'weekend';
    if (this.holidays.has(n + 1)) return 'preholiday';
    if (this.workdays.has(n)) return 'transfer';
    return 'workday';
  }

  /** Working hours of day number `n`. */
  hours(n) {
    const k = this.kind(n);
    if (k === 'preholiday') return this.dayHours - this.cut;
    if (k === 'workday' || k === 'transfer') return this.dayHours;
    return 0;
  }
}

/**
 * Validate a spec and build the internal representation.
 * @param {object|null|undefined} spec
 * @returns {Cal}
 */
function buildCal(spec) {
  const cal = new Cal();
  if (spec == null) spec = {};
  needDict(spec, 'calendar');
  for (const key of Object.keys(spec)) {
    if (!CAL_KEYS.includes(key)) fail(E_TYPE, 'unknown calendar key');
  }
  let weekend = spec.weekend;
  if (weekend == null) weekend = DEFAULT_WEEKEND.slice();
  needList(weekend, 'weekend');
  cal.weekend = new Set(weekend.map((c) => weekdayIndex(c, 'weekend day')));
  if (cal.weekend.size > 6) fail(E_RANGE, 'a calendar needs at least one working weekday');
  const holidays = spec.holidays == null ? [] : needList(spec.holidays, 'holidays');
  cal.holidays = new Set(holidays.map((x) => toDays(x, 'holiday')));
  const workdays = spec.workdays == null ? [] : needList(spec.workdays, 'workdays');
  cal.workdays = new Set(workdays.map((x) => toDays(x, 'workday')).filter((n) => !cal.holidays.has(n)));
  const hours = spec.dayHours == null ? 8 : needInt(spec.dayHours, 'dayHours');
  if (hours < 1 || hours > 24) fail(E_RANGE, 'dayHours out of range');
  const cut = spec.preholidayCut == null ? 1 : needInt(spec.preholidayCut, 'preholidayCut');
  if (cut < 0 || cut >= hours) fail(E_RANGE, 'preholidayCut out of range');
  cal.dayHours = hours;
  cal.cut = cut;
  return cal;
}

/**
 * Validate a spec and return its normalised form: every key present,
 * `weekend` codes sorted Monday→Sunday without duplicates, `holidays` and
 * `workdays` sorted ascending without duplicates, and dates that are both
 * holidays and workdays removed from `workdays`.
 *
 * The result is itself a valid spec, so it can be stored and passed back.
 *
 * @param {object|null} [spec]
 * @returns {{weekend: string[], holidays: string[], workdays: string[], dayHours: number, preholidayCut: number}}
 * @example makeCalendar({ weekend: ['SU', 'SA', 'SU'] })
 *   // { weekend: ['SA', 'SU'], holidays: [], workdays: [], dayHours: 8, preholidayCut: 1 }
 */
function makeCalendar(spec = null) {
  const cal = buildCal(spec);
  return {
    weekend: Array.from(cal.weekend).sort(byNumber).map((i) => WEEKDAYS[i]),
    holidays: Array.from(cal.holidays).sort(byNumber).map(iso),
    workdays: Array.from(cal.workdays).sort(byNumber).map(iso),
    dayHours: cal.dayHours,
    preholidayCut: cal.cut,
  };
}

/**
 * Kind of a day: `'holiday'`, `'weekend'`, `'preholiday'`, `'transfer'` or
 * `'workday'` (see the precedence rules in the module comment).
 *
 * @param {string} date
 * @param {object|null} [calendar]
 * @returns {string}
 * @example dayKind('2024-05-08', { holidays: ['2024-05-09'] }) // 'preholiday'
 * @example dayKind('2024-04-27', { workdays: ['2024-04-27'] }) // 'transfer' (a Saturday)
 */
function dayKind(date, calendar = null) {
  const n = toDays(date);
  return buildCal(calendar).kind(n);
}

/**
 * Working day? True for the kinds workday, transfer and preholiday.
 *
 * @param {string} date
 * @param {object|null} [calendar]
 * @returns {boolean}
 */
function isWorkday(date, calendar = null) {
  const n = toDays(date);
  return buildCal(calendar).working(n);
}

/**
 * Listed public holiday? Weekends are not holidays.
 *
 * @param {string} date
 * @param {object|null} [calendar]
 * @returns {boolean}
 */
function isHoliday(date, calendar = null) {
  const n = toDays(date);
  return buildCal(calendar).holidays.has(n);
}

/**
 * Normative working hours of a day: `dayHours` for a workday or transfer,
 * `dayHours - preholidayCut` for a preholiday, 0 otherwise.
 *
 * @param {string} date
 * @param {object|null} [calendar]
 * @returns {number}
 */
function workHours(date, calendar = null) {
  const n = toDays(date);
  return buildCal(calendar).hours(n);
}

/**
 * Everything about one day in a single object.
 *
 * @param {string} date
 * @param {object|null} [calendar]
 * @returns {{date: string, kind: string, isWorkday: boolean, hours: number, weekday: string}}
 */
function dayInfo(date, calendar = null) {
  const n = toDays(date);
  const cal = buildCal(calendar);
  const kind = cal.kind(n);
  return {
    date: iso(n),
    kind,
    isWorkday: kind === 'workday' || kind === 'transfer' || kind === 'preholiday',
    hours: cal.hours(n),
    weekday: WEEKDAYS[wd(n)],
  };
}

/**
 * Listed holidays within `[start, end]` (both inclusive), ascending. Empty
 * when `end` is before `start`.
 *
 * @param {string} start
 * @param {string} end
 * @param {object|null} [calendar]
 * @returns {string[]}
 */
function holidaysBetween(start, end, calendar = null) {
  const a = toDays(start);
  const b = toDays(end);
  const cal = buildCal(calendar);
  return Array.from(cal.holidays).sort(byNumber).filter((n) => a <= n && n <= b).map(iso);
}

/**
 * Combine two calendars (e.g. the federal calendar and regional holidays).
 *
 * - `holidays` and `workdays` are the unions of both;
 * - `weekend`, `dayHours` and `preholidayCut` are taken from `extra` when
 *   `extra` sets them (non-null), otherwise from `base`;
 * - the usual precedence applies to the union, so a holiday of either
 *   calendar beats a transferred working day of the other.
 *
 * `base` may be null (default calendar); `extra` must be an object. The
 * result is normalised like {@link makeCalendar} and validated again (so an
 * `extra.dayHours` that is smaller than the inherited cut fails with
 * `E_RANGE`).
 *
 * @param {object|null} base
 * @param {object} extra
 * @returns {object} normalised spec
 */
function mergeCalendars(base, extra) {
  const a = makeCalendar(base);
  needDict(extra, 'calendar');
  const b = makeCalendar(extra);
  return makeCalendar({
    weekend: extra.weekend != null ? b.weekend : a.weekend,
    holidays: a.holidays.concat(b.holidays),
    workdays: a.workdays.concat(b.workdays),
    dayHours: extra.dayHours != null ? b.dayHours : a.dayHours,
    preholidayCut: extra.preholidayCut != null ? b.preholidayCut : a.preholidayCut,
  });
}

module.exports = {
  Cal,
  buildCal,
  makeCalendar,
  dayKind,
  isWorkday,
  isHoliday,
  workHours,
  dayInfo,
  holidaysBetween,
  mergeCalendars,
};
