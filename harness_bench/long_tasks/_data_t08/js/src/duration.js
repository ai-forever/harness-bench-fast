'use strict';

/**
 * @module duration
 *
 * Calendar durations in a subset of ISO 8601: years, months, weeks, days.
 * There is no time part (`PT1H` is `E_PARSE`).
 *
 * ## Text form
 *
 * `[+|-]P[nY][nM][nW][nD]` — components in exactly this order, each at most
 * once, at least one present, non-negative integers. Unlike strict ISO 8601
 * weeks **may** be combined with the other units (`P1M2W`). The letters are
 * upper case. Surrounding whitespace is ignored. Each component must be at
 * most 99999 (`E_RANGE`).
 *
 * ## Object form
 *
 * `{ sign: 1 | -1, years, months, weeks, days }` — the sign is carried
 * separately and every component is a non-negative integer. When an object
 * is *accepted* as input, missing or null keys default to 0 (sign to 1);
 * unknown keys are `E_TYPE`; a non-integer value is `E_TYPE`; a negative or
 * too large component or a sign other than ±1 is `E_RANGE`.
 *
 * ## Applying a duration
 *
 * {@link addDuration} converts years to months (`12 * years + months`) and
 * weeks to days (`7 * weeks + days`), multiplies both by the sign, then
 * applies the **months first, using overflow semantics** (civil.js
 * `addMonths`) and the days second. The order holds for negative durations
 * too:
 *
 *     addDuration('2024-01-31', 'P1M1D')  // '2024-03-03'  (Jan 31 + 1M = Mar 2, + 1D)
 *     addDuration('2024-03-31', '-P1M1D') // '2024-03-01'  (Mar 31 - 1M = Mar 2, - 1D)
 *
 * {@link diffDates} measures the other way round and **clamps** (it uses
 * `addMonthsClamped` for its anchor). The two are therefore not exact
 * inverses at month ends — this is documented, tested and relied upon by the
 * contract-term report. Do not "fix" one of them.
 */

const { daysFromCivil, dim, isInt, iso, needStr, toDays, ymd, div, mod } = require('./internal');
const { addMonths, addMonthsClamped } = require('./civil');
const { fail, E_PARSE, E_RANGE, E_TYPE } = require('./errors');

const RE = /^([+-])?P(?:([0-9]+)Y)?(?:([0-9]+)M)?(?:([0-9]+)W)?(?:([0-9]+)D)?$/;
const FIELDS = Object.freeze(['years', 'months', 'weeks', 'days']);
const MAX_COMPONENT = 99999;

/**
 * Parse duration text into the object form.
 *
 * @param {string} text
 * @returns {{sign: number, years: number, months: number, weeks: number, days: number}}
 * @throws {ChronoError} E_TYPE, E_PARSE (bad shape, or no component at all: `'P'`), E_RANGE
 * @example parseDuration('P1Y2M')  // { sign: 1, years: 1, months: 2, weeks: 0, days: 0 }
 * @example parseDuration(' -P3W ') // { sign: -1, years: 0, months: 0, weeks: 3, days: 0 }
 */
function parseDuration(text) {
  needStr(text, 'duration');
  const s = text.trim();
  const mt = RE.exec(s);
  if (!mt || mt.slice(2).every((g) => g === undefined)) fail(E_PARSE, 'bad duration');
  const out = { sign: mt[1] === '-' ? -1 : 1 };
  FIELDS.forEach((name, i) => {
    const g = mt[i + 2];
    const v = g !== undefined ? parseInt(g, 10) : 0;
    if (v > MAX_COMPONENT) fail(E_RANGE, 'duration component too large');
    out[name] = v;
  });
  return out;
}

/**
 * Accept a duration string or object and return a fresh normalised object.
 *
 * @param {string|object} value
 * @returns {{sign: number, years: number, months: number, weeks: number, days: number}}
 */
function toDuration(value) {
  if (typeof value === 'string') return parseDuration(value);
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    fail(E_TYPE, 'duration must be a string or an object');
  }
  for (const key of Object.keys(value)) {
    if (key !== 'sign' && !FIELDS.includes(key)) fail(E_TYPE, 'unknown duration key');
  }
  const sign = value.sign == null ? 1 : value.sign;
  if (!isInt(sign)) fail(E_TYPE, 'sign must be an integer');
  if (sign !== 1 && sign !== -1) fail(E_RANGE, 'sign must be 1 or -1');
  const out = { sign };
  for (const name of FIELDS) {
    const v = value[name] == null ? 0 : value[name];
    if (!isInt(v)) fail(E_TYPE, 'duration components must be integers');
    if (v < 0 || v > MAX_COMPONENT) fail(E_RANGE, 'duration component out of range');
    out[name] = v;
  }
  return out;
}

/**
 * Canonical text form. Zero components are omitted; an all-zero duration is
 * `'P0D'` regardless of its sign (there is no `'-P0D'`).
 *
 * @param {string|object} value
 * @returns {string}
 * @example formatDuration({ months: 14 })              // 'P14M'  (no normalisation into years)
 * @example formatDuration('P0Y0M1W')                   // 'P1W'
 * @example formatDuration({ sign: -1, days: 0 })       // 'P0D'
 */
function formatDuration(value) {
  const dur = toDuration(value);
  let body = '';
  const letters = 'YMWD';
  FIELDS.forEach((name, i) => {
    if (dur[name]) body += `${dur[name]}${letters[i]}`;
  });
  if (!body) return 'P0D';
  return (dur.sign < 0 ? '-' : '') + 'P' + body;
}

/**
 * Add a duration to a date (months first with overflow, then days — see
 * the module comment).
 *
 * @param {string} date
 * @param {string|object} value
 * @returns {string}
 * @example addDuration('2023-01-31', 'P1M')  // '2023-03-03'
 * @example addDuration('2024-02-29', 'P1Y')  // '2025-03-01'
 * @example addDuration('2024-01-01', 'P2W3D') // '2024-01-18'
 */
function addDuration(date, value) {
  toDays(date);
  const dur = toDuration(value);
  const months = dur.sign * (dur.years * 12 + dur.months);
  const days = dur.sign * (dur.weeks * 7 + dur.days);
  const shifted = addMonths(date, months);
  return iso(toDays(shifted) + days);
}

/**
 * `addDuration` with the sign flipped. Note that this is *not* the inverse
 * of `addDuration`: `subtractDuration(addDuration(d, x), x)` is not always
 * `d` at month ends.
 *
 * @param {string} date
 * @param {string|object} value
 * @returns {string}
 */
function subtractDuration(date, value) {
  const dur = toDuration(value);
  dur.sign = -dur.sign;
  return addDuration(date, dur);
}

/**
 * Normalised object with the opposite sign (even for a zero duration).
 * @param {string|object} value
 * @returns {object}
 */
function negateDuration(value) {
  const dur = toDuration(value);
  dur.sign = -dur.sign;
  return dur;
}

/**
 * How many days the duration spans when applied at `start`
 * (`addDuration(start, value) - start`, may be negative).
 *
 * @param {string|object} value
 * @param {string} start
 * @returns {number}
 * @example durationDays('P1M', '2024-02-01') // 29
 */
function durationDays(value, start) {
  return toDays(addDuration(start, value)) - toDays(start);
}

/**
 * Difference between two dates as years, months and days.
 *
 * Algorithm (dates swapped first when `end < start`, then `sign = -1`):
 *
 * 1. `months = 12 * (y2 - y1) + (m2 - m1)`, minus one when `d2 < d1`;
 * 2. `anchor = addMonthsClamped(start, months)`;
 * 3. `days = end - anchor` (in days).
 *
 * `years = floor(months / 12)`, `months = months mod 12`.
 *
 * @param {string} start
 * @param {string} end
 * @returns {{sign: number, years: number, months: number, days: number}}
 * @example diffDates('2024-01-31', '2024-03-01') // { sign: 1, years: 0, months: 1, days: 1 }
 * @example diffDates('2024-03-01', '2024-01-31') // { sign: -1, years: 0, months: 1, days: 1 }
 */
function diffDates(start, end) {
  let a = toDays(start);
  let b = toDays(end);
  let sign = 1;
  if (b < a) [a, b, sign] = [b, a, -1];
  const [ya, ma, da] = ymd(a);
  const [yb, mb, db] = ymd(b);
  let months = (yb - ya) * 12 + (mb - ma);
  if (db < da) months -= 1;
  const anchor = toDays(addMonthsClamped(iso(a), months));
  return { sign, years: div(months, 12), months: mod(months, 12), days: b - anchor };
}

/**
 * Whole months between two dates, signed — the months part of
 * {@link diffDates} (`sign * (12 * years + months)`).
 *
 * @param {string} start
 * @param {string} end
 * @returns {number}
 */
function monthsBetween(start, end) {
  const d = diffDates(start, end);
  return d.sign * (d.years * 12 + d.months);
}

/**
 * Age in full years on a date. Someone born on 29 February becomes one year
 * older on **1 March** in common years (the comparison is on (month, day)
 * pairs, and (2, 28) < (2, 29)).
 *
 * @param {string} birth
 * @param {string} date
 * @returns {number}
 * @throws {ChronoError} E_RANGE when date is before birth
 */
function ageOn(birth, date) {
  const a = toDays(birth);
  const b = toDays(date);
  if (b < a) fail(E_RANGE, 'date before birth');
  const [ya, ma, da] = ymd(a);
  const [yb, mb, db] = ymd(b);
  let years = yb - ya;
  if (mb < ma || (mb === ma && db < da)) years -= 1;
  return years;
}

/**
 * Last day of the month that is `n` months after the month of `date`
 * (clamped arithmetic, so the day of `date` does not matter).
 *
 * @param {string} date
 * @param {number} n
 * @returns {string}
 * @example endOfMonthAfter('2024-01-31', 1) // '2024-02-29'
 */
function endOfMonthAfter(date, n) {
  const [y, m] = ymd(toDays(addMonthsClamped(date, n)));
  return iso(daysFromCivil(y, m, dim(y, m)));
}

module.exports = {
  parseDuration,
  toDuration,
  formatDuration,
  addDuration,
  subtractDuration,
  negateDuration,
  durationDays,
  diffDates,
  monthsBetween,
  ageOn,
  endOfMonthAfter,
};
