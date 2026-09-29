'use strict';

/**
 * @module rrule
 *
 * RRULE-lite: a date-only subset of the iCalendar (RFC 5545) recurrence rule.
 *
 * ## Syntax
 *
 * `KEY=VALUE` parts separated by `;`, optionally prefixed with `RRULE:`.
 * The whole text is trimmed and **upper-cased** first, so keys and values
 * are case-insensitive. Empty parts are ignored (`FREQ=DAILY;;COUNT=3;` is
 * fine) but spaces are not trimmed inside the text (`FREQ = DAILY` has the
 * unknown key `"FREQ "`). Every key may appear once. **Every** problem in a
 * rule — unknown key, bad number, impossible UNTIL date — is `E_RULE`.
 *
 * | key          | value                                                    |
 * |--------------|----------------------------------------------------------|
 * | `FREQ`       | required: `DAILY`, `WEEKLY`, `MONTHLY` or `YEARLY`       |
 * | `INTERVAL`   | 1..1000, default 1                                       |
 * | `COUNT`      | 1..10000; cannot be combined with UNTIL                   |
 * | `UNTIL`      | `YYYYMMDD` or `YYYY-MM-DD`, **inclusive**                 |
 * | `BYMONTH`    | comma list of 1..12                                      |
 * | `BYMONTHDAY` | comma list of ±1..31 (negative = from month end); not with WEEKLY |
 * | `BYDAY`      | comma list of weekday codes with an optional ordinal: `MO`, `2TU`, `-1FR`, `+3WE` |
 * | `BYSETPOS`   | comma list of ±1..366; needs BYMONTH, BYMONTHDAY or BYDAY |
 * | `WKST`       | weekday code the week starts on (WEEKLY), default `MO`    |
 *
 * Numbers are decimal digits; list items may carry a sign (`+5`, `-1`), the
 * scalar values INTERVAL and COUNT may not. Zero is never allowed in a list.
 *
 * BYDAY ordinals are only allowed with MONTHLY and YEARLY. Their range is
 * ±1..5 for MONTHLY, and for YEARLY when BYMONTH is present (the ordinal is
 * then relative to each month); for YEARLY without BYMONTH the ordinal is
 * relative to the whole year and may be ±1..53. An ordinal BYDAY cannot be
 * combined with BYMONTHDAY.
 *
 * ## Normalised object
 *
 * {@link parseRRule} returns
 *
 * ```js
 * { freq, interval, count, until, bymonth, bymonthday, byday, bysetpos, wkst }
 * ```
 *
 * with `count`/`until` `null` when absent (`until` as `"YYYY-MM-DD"`),
 * `bymonth` and `bymonthday` **deduplicated and sorted ascending**, `byday`
 * and `bysetpos` **deduplicated in order of first appearance** (not sorted!),
 * BYDAY items canonical (`'+2MO'` → `'2MO'`), and every list present (empty
 * when absent). Functions that take a rule accept either the text or such an
 * object (the object is converted back to text and parsed again, so it is
 * validated the same way; a field of the wrong JavaScript type is `E_TYPE`).
 */

const { WEEKDAYS, daysFromCivil, dim, isInt, iso, makeDays, needInt, toDays, wd, ymd, mod, div, byNumber } = require('./internal');
const { ChronoError, fail, E_LIMIT, E_RANGE, E_RULE, E_TYPE } = require('./errors');

const FREQS = Object.freeze(['DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY']);
const KEYS = Object.freeze(['FREQ', 'INTERVAL', 'COUNT', 'UNTIL', 'BYMONTH', 'BYMONTHDAY', 'BYDAY', 'BYSETPOS', 'WKST']);
const RULE_FIELDS = Object.freeze(['freq', 'interval', 'count', 'until', 'bymonth', 'bymonthday', 'byday', 'bysetpos', 'wkst']);
/** Expansion never looks at more than this many periods (days/weeks/months/years). */
const MAX_PERIODS = 50000;
const MAX_INTERVAL = 1000;
const MAX_COUNT = 10000;

const RE_UINT = /^[0-9]+$/;
const RE_SINT = /^[+-]?[0-9]+$/;
const RE_DAY = /^([+-]?[0-9]{1,2})?(MO|TU|WE|TH|FR|SA|SU)$/;
const RE_UNTIL = /^([0-9]{4})-?([0-9]{2})-?([0-9]{2})$/;

/** Parsed rule (internal). `days` holds [ordinal, weekdayNumber] pairs, ordinal 0 = none. */
class Rule {
  toObject() {
    return {
      freq: this.freq,
      interval: this.interval,
      count: this.count,
      until: this.until === null ? null : iso(this.until),
      bymonth: this.bymonth.slice(),
      bymonthday: this.bymonthday.slice(),
      byday: this.days.map(([n, w]) => (n ? `${n}${WEEKDAYS[w]}` : WEEKDAYS[w])),
      bysetpos: this.bysetpos.slice(),
      wkst: this.wkst,
    };
  }
}

function uint(text, lo, hi) {
  if (!RE_UINT.test(text)) fail(E_RULE, 'expected a positive integer');
  const v = parseInt(text, 10);
  if (v < lo || v > hi) fail(E_RULE, 'number out of range');
  return v;
}

function intList(text, lo, hi) {
  const out = [];
  for (const item of text.split(',')) {
    if (!RE_SINT.test(item)) fail(E_RULE, 'bad number in list');
    const v = parseInt(item, 10);
    if (v === 0 || v < lo || v > hi) fail(E_RULE, 'number out of range');
    if (!out.includes(v)) out.push(v);
  }
  return out;
}

/** Parse rule text into a {@link Rule}. */
function parseText(text) {
  let s = text.trim().toUpperCase();
  if (s.startsWith('RRULE:')) s = s.slice(6);
  const parts = s.split(';').filter((p) => p !== '');
  if (parts.length === 0) fail(E_RULE, 'empty rule');
  const seen = new Map();
  for (const part of parts) {
    const eq = part.indexOf('=');
    if (eq < 0) fail(E_RULE, 'expected KEY=VALUE');
    const key = part.slice(0, eq);
    const value = part.slice(eq + 1);
    if (!KEYS.includes(key)) fail(E_RULE, 'unknown key');
    if (seen.has(key)) fail(E_RULE, 'duplicate key');
    seen.set(key, value);
  }
  const r = new Rule();
  if (!seen.has('FREQ') || !FREQS.includes(seen.get('FREQ'))) fail(E_RULE, 'FREQ is required');
  r.freq = seen.get('FREQ');
  r.interval = seen.has('INTERVAL') ? uint(seen.get('INTERVAL'), 1, MAX_INTERVAL) : 1;
  r.count = seen.has('COUNT') ? uint(seen.get('COUNT'), 1, MAX_COUNT) : null;
  r.until = null;
  if (seen.has('UNTIL')) {
    const mt = RE_UNTIL.exec(seen.get('UNTIL'));
    if (!mt) fail(E_RULE, 'bad UNTIL');
    try {
      r.until = makeDays(parseInt(mt[1], 10), parseInt(mt[2], 10), parseInt(mt[3], 10));
    } catch (err) {
      if (err instanceof ChronoError) fail(E_RULE, 'bad UNTIL date');
      throw err;
    }
  }
  if (r.count !== null && r.until !== null) fail(E_RULE, 'COUNT and UNTIL are exclusive');
  r.bymonth = seen.has('BYMONTH') ? intList(seen.get('BYMONTH'), 1, 12).sort(byNumber) : [];
  r.bymonthday = seen.has('BYMONTHDAY') ? intList(seen.get('BYMONTHDAY'), -31, 31).sort(byNumber) : [];
  if (r.bymonthday.length && r.freq === 'WEEKLY') fail(E_RULE, 'BYMONTHDAY is not allowed with WEEKLY');
  r.days = [];
  if (seen.has('BYDAY')) {
    for (const item of seen.get('BYDAY').split(',')) {
      const mt = RE_DAY.exec(item);
      if (!mt) fail(E_RULE, 'bad BYDAY item');
      const n = mt[1] ? parseInt(mt[1], 10) : 0;
      if (mt[1] && n === 0) fail(E_RULE, 'ordinal must not be zero');
      if (n) {
        if (r.freq !== 'MONTHLY' && r.freq !== 'YEARLY') fail(E_RULE, 'ordinal BYDAY needs MONTHLY or YEARLY');
        const top = r.freq === 'MONTHLY' || r.bymonth.length ? 5 : 53;
        if (Math.abs(n) > top) fail(E_RULE, 'ordinal out of range');
      }
      const w = WEEKDAYS.indexOf(mt[2]);
      if (!r.days.some(([a, b]) => a === n && b === w)) r.days.push([n, w]);
    }
  }
  if (r.bymonthday.length && r.days.some(([n]) => n !== 0)) {
    fail(E_RULE, 'ordinal BYDAY cannot be combined with BYMONTHDAY');
  }
  r.bysetpos = seen.has('BYSETPOS') ? intList(seen.get('BYSETPOS'), -366, 366) : [];
  if (r.bysetpos.length && !(r.bymonth.length || r.bymonthday.length || r.days.length)) {
    fail(E_RULE, 'BYSETPOS needs another BYxxx part');
  }
  if (seen.has('WKST')) {
    if (!WEEKDAYS.includes(seen.get('WKST'))) fail(E_RULE, 'bad WKST');
    r.wkst = seen.get('WKST');
  } else {
    r.wkst = 'MO';
  }
  return r;
}

/**
 * Convert a rule object back into text (validating JavaScript types only;
 * the text is then parsed by {@link parseText}).
 */
function objectToText(obj) {
  for (const key of Object.keys(obj)) {
    if (!RULE_FIELDS.includes(key)) fail(E_TYPE, 'unknown rule field');
  }
  if (typeof obj.freq !== 'string') fail(E_TYPE, 'freq must be a string');
  const parts = [`FREQ=${obj.freq}`];
  if (obj.interval != null) {
    needInt(obj.interval, 'interval');
    if (obj.interval !== 1) parts.push(`INTERVAL=${obj.interval}`);
  }
  if (obj.count != null) {
    needInt(obj.count, 'count');
    parts.push(`COUNT=${obj.count}`);
  }
  if (obj.until != null) {
    if (typeof obj.until !== 'string') fail(E_TYPE, 'until must be a string');
    parts.push(`UNTIL=${obj.until}`);
  }
  for (const [field, key] of [['bymonth', 'BYMONTH'], ['bymonthday', 'BYMONTHDAY'], ['bysetpos', 'BYSETPOS']]) {
    const items = obj[field];
    if (items == null) continue;
    if (!Array.isArray(items) || !items.every(isInt)) fail(E_TYPE, `${field} must be an array of integers`);
    if (items.length) parts.push(`${key}=${items.join(',')}`);
  }
  if (obj.byday != null) {
    if (!Array.isArray(obj.byday) || !obj.byday.every((v) => typeof v === 'string')) {
      fail(E_TYPE, 'byday must be an array of strings');
    }
    if (obj.byday.length) parts.push(`BYDAY=${obj.byday.join(',')}`);
  }
  if (obj.wkst != null) {
    if (typeof obj.wkst !== 'string') fail(E_TYPE, 'wkst must be a string');
    parts.push(`WKST=${obj.wkst}`);
  }
  return parts.join(';');
}

/**
 * Rule text or rule object → internal {@link Rule}.
 * @param {string|object} rule
 * @returns {Rule}
 * @throws {ChronoError} E_TYPE for other types
 */
function toRule(rule) {
  if (typeof rule === 'string') return parseText(rule);
  if (rule !== null && typeof rule === 'object' && !Array.isArray(rule)) return parseText(objectToText(rule));
  return fail(E_TYPE, 'rule must be a string or an object');
}

/**
 * Parse rule **text** into the normalised object (see the module comment).
 * Unlike the other functions this one does not accept an object.
 *
 * @param {string} text
 * @returns {object}
 * @example parseRRule('rrule:freq=monthly;byday=+1mo,-1fr,1MO;bymonth=12,3')
 *   // { freq: 'MONTHLY', interval: 1, count: null, until: null, bymonth: [3, 12],
 *   //   bymonthday: [], byday: ['1MO', '-1FR'], bysetpos: [], wkst: 'MO' }
 */
function parseRRule(text) {
  if (typeof text !== 'string') fail(E_TYPE, 'rule text must be a string');
  return parseText(text).toObject();
}

/**
 * Canonical text of a rule: keys in the fixed order FREQ, INTERVAL (only
 * when not 1), COUNT, UNTIL (as `YYYYMMDD`), BYMONTH, BYMONTHDAY, BYDAY,
 * BYSETPOS, WKST (only when not MO); lists in their normalised order.
 *
 * @param {string|object} rule
 * @returns {string}
 * @example stringifyRRule('byday=fr,mo;freq=weekly;interval=1;wkst=mo') // 'FREQ=WEEKLY;BYDAY=FR,MO'
 */
function stringifyRRule(rule) {
  const r = toRule(rule);
  const parts = [`FREQ=${r.freq}`];
  if (r.interval !== 1) parts.push(`INTERVAL=${r.interval}`);
  if (r.count !== null) parts.push(`COUNT=${r.count}`);
  if (r.until !== null) parts.push(`UNTIL=${iso(r.until).replace(/-/g, '')}`);
  if (r.bymonth.length) parts.push(`BYMONTH=${r.bymonth.join(',')}`);
  if (r.bymonthday.length) parts.push(`BYMONTHDAY=${r.bymonthday.join(',')}`);
  if (r.days.length) parts.push(`BYDAY=${r.toObject().byday.join(',')}`);
  if (r.bysetpos.length) parts.push(`BYSETPOS=${r.bysetpos.join(',')}`);
  if (r.wkst !== 'MO') parts.push(`WKST=${r.wkst}`);
  return parts.join(';');
}

/* --------------------------------------------------------------------------
 * Expansion
 *
 * The rule is expanded period by period. Period k (k = 0, 1, 2, ...) is:
 *
 * - DAILY:   the single day `dtstart + k * INTERVAL`;
 * - WEEKLY:  the 7-day week starting on WKST that contains dtstart, plus
 *            `k * INTERVAL` weeks;
 * - MONTHLY: the month of dtstart plus `k * INTERVAL` months;
 * - YEARLY:  the year of dtstart plus `k * INTERVAL` years.
 *
 * Inside a period the *candidates* are computed (see below), sorted, BYSETPOS
 * is applied to them, and then they are emitted in ascending order, where
 *
 * - candidates **before dtstart are dropped** (they do not count for COUNT);
 * - **dtstart itself is only an occurrence when it matches the rule** — this
 *   differs from RFC 5545, where DTSTART always counts as the first
 *   instance. `expandRRule('FREQ=MONTHLY;BYDAY=-1FR;COUNT=2', '2024-03-01')`
 *   starts on 29 March, not on 1 March;
 * - a candidate after UNTIL ends the expansion (UNTIL is inclusive);
 * - COUNT counts emitted occurrences and ends the expansion when reached.
 *
 * The expansion also ends when a period starts after UNTIL, when a period
 * would lie beyond year 9999, or after MAX_PERIODS periods — then the
 * occurrences found so far are the result (no error). A rule that can never
 * match (`FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30`) therefore simply yields
 * nothing.
 *
 * Candidates per frequency:
 *
 * DAILY — the day itself if it passes all filters: month in BYMONTH, day of
 * month in BYMONTHDAY (negative values counted from the month end), weekday
 * in BYDAY.
 *
 * WEEKLY — the days of the week whose weekday is in BYDAY (or, without
 * BYDAY, the day with dtstart's weekday), then filtered by BYMONTH.
 *
 * MONTHLY — nothing when BYMONTH is given and does not contain the month.
 * Without BYMONTHDAY and BYDAY: the day with dtstart's day of month —
 * **months that do not have that day are skipped** (a rule started on the
 * 31st only fires in 31-day months; there is no overflow here, unlike
 * civil.js addMonths). With BYMONTHDAY: those days (non-existent ones such as
 * 30 February or -31 in a 30-day month are dropped), further restricted to
 * the BYDAY weekdays if BYDAY is given. With BYDAY only: plain weekdays give
 * every such weekday of the month, ordinals give the n-th one (absent ones
 * are skipped).
 *
 * YEARLY — without BYMONTHDAY and BYDAY: dtstart's day in each BYMONTH month
 * (or in dtstart's month), skipped where it does not exist (so a rule started
 * on 29 February only fires in leap years). With BYMONTHDAY, or with BYDAY
 * and BYMONTH: the MONTHLY logic applied to each BYMONTH month (all twelve
 * months when BYMONTH is absent). With BYDAY but no BYMONTH and no
 * BYMONTHDAY: weekdays relative to the **whole year** — plain codes give
 * every such weekday of the year, `20MO` the 20th Monday, `-1SU` the last
 * Sunday of the year.
 *
 * BYSETPOS picks positions (1-based, negative from the end) from the sorted
 * candidates of one period; positions outside the list are ignored and the
 * picked dates are emitted in ascending order without duplicates.
 * ------------------------------------------------------------------------ */

/** Days of month (sorted) produced by BYMONTHDAY/BYDAY for one month. */
function monthDays(r, y, m, d0) {
  const last = dim(y, m);
  if (!r.bymonthday.length && !r.days.length) return d0 <= last ? [d0] : [];
  let days = new Set();
  const first = daysFromCivil(y, m, 1);
  if (r.bymonthday.length) {
    for (const v of r.bymonthday) {
      const dd = v > 0 ? v : last + v + 1;
      if (dd >= 1 && dd <= last) days.add(dd);
    }
    if (r.days.length) {
      const wanted = new Set(r.days.map(([, w]) => w));
      days = new Set(Array.from(days).filter((dd) => wanted.has(wd(first + dd - 1))));
    }
  } else {
    for (const [n, w] of r.days) {
      if (n === 0) {
        for (let dd = 1 + mod(w - wd(first), 7); dd <= last; dd += 7) days.add(dd);
      } else if (n > 0) {
        const dd = 1 + mod(w - wd(first), 7) + (n - 1) * 7;
        if (dd <= last) days.add(dd);
      } else {
        const end = first + last - 1;
        const dd = last - mod(wd(end) - w, 7) - (-n - 1) * 7;
        if (dd >= 1) days.add(dd);
      }
    }
  }
  return Array.from(days).sort(byNumber);
}

/** Candidate day numbers of year y for a YEARLY rule. */
function yearCandidates(r, y, m0, d0) {
  const out = [];
  if (!r.bymonthday.length && !r.days.length) {
    for (const m of r.bymonth.length ? r.bymonth : [m0]) {
      if (d0 <= dim(y, m)) out.push(daysFromCivil(y, m, d0));
    }
    return out;
  }
  if (r.bymonthday.length || r.bymonth.length) {
    const months = r.bymonth.length ? r.bymonth : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
    for (const m of months) {
      const first = daysFromCivil(y, m, 1);
      for (const dd of monthDays(r, y, m, d0)) out.push(first + dd - 1);
    }
    return out;
  }
  const jan1 = daysFromCivil(y, 1, 1);
  const dec31 = daysFromCivil(y, 12, 31);
  const found = new Set();
  for (const [n, w] of r.days) {
    if (n === 0) {
      for (let x = jan1 + mod(w - wd(jan1), 7); x <= dec31; x += 7) found.add(x);
    } else if (n > 0) {
      const x = jan1 + mod(w - wd(jan1), 7) + (n - 1) * 7;
      if (x <= dec31) found.add(x);
    } else {
      const x = dec31 - mod(wd(dec31) - w, 7) - (-n - 1) * 7;
      if (x >= jan1) found.add(x);
    }
  }
  return Array.from(found).sort(byNumber);
}

/** DAILY filter. */
function dayMatches(r, n) {
  const [y, m, d] = ymd(n);
  if (r.bymonth.length && !r.bymonth.includes(m)) return false;
  if (r.bymonthday.length) {
    const last = dim(y, m);
    if (!r.bymonthday.some((v) => (v > 0 ? v : last + v + 1) === d)) return false;
  }
  if (r.days.length && !r.days.some(([, w]) => w === wd(n))) return false;
  return true;
}

/**
 * Period k of the rule: `[periodStartDay, sortedCandidates]`, or null when
 * the period lies beyond year 9999.
 */
function period(r, k, start) {
  const [y0, m0, d0] = ymd(start);
  if (r.freq === 'DAILY') {
    const day = start + k * r.interval;
    if (ymd(day)[0] > 9999) return null;
    return [day, dayMatches(r, day) ? [day] : []];
  }
  if (r.freq === 'WEEKLY') {
    const ws = WEEKDAYS.indexOf(r.wkst);
    const week0 = start - mod(wd(start) - ws, 7);
    const pstart = week0 + 7 * r.interval * k;
    if (ymd(pstart)[0] > 9999) return null;
    const wanted = r.days.length ? new Set(r.days.map(([, w]) => w)) : new Set([wd(start)]);
    let cands = [];
    for (let x = pstart; x < pstart + 7; x += 1) if (wanted.has(wd(x))) cands.push(x);
    if (r.bymonth.length) cands = cands.filter((x) => r.bymonth.includes(ymd(x)[1]));
    return [pstart, cands];
  }
  if (r.freq === 'MONTHLY') {
    const total = y0 * 12 + (m0 - 1) + k * r.interval;
    const y = div(total, 12);
    const m = total - y * 12 + 1;
    if (y > 9999) return null;
    const first = daysFromCivil(y, m, 1);
    if (r.bymonth.length && !r.bymonth.includes(m)) return [first, []];
    return [first, monthDays(r, y, m, d0).map((dd) => first + dd - 1)];
  }
  const y = y0 + k * r.interval;
  if (y > 9999) return null;
  return [daysFromCivil(y, 1, 1), yearCandidates(r, y, m0, d0)];
}

function applySetpos(r, cands) {
  if (!r.bysetpos.length || !cands.length) return cands;
  const picked = new Set();
  for (const p of r.bysetpos) {
    const idx = p > 0 ? p - 1 : cands.length + p;
    if (idx >= 0 && idx < cands.length) picked.add(cands[idx]);
  }
  return Array.from(picked).sort(byNumber);
}

/**
 * Generator of occurrence day numbers, ascending, with COUNT and UNTIL
 * applied. `stop` (a day number or null) ends the walk as soon as a period
 * starts after it; occurrences after `stop` inside the last period may still
 * be yielded — callers filter.
 */
function* iterate(r, start, stop = null) {
  let count = 0;
  for (let k = 0; k < MAX_PERIODS; k += 1) {
    const p = period(r, k, start);
    if (p === null) return;
    const [pstart, cands] = p;
    if (r.until !== null && pstart > r.until) return;
    if (stop !== null && pstart > stop) return;
    for (const c of applySetpos(r, cands)) {
      if (c < start) continue;
      if (r.until !== null && c > r.until) return;
      if (ymd(c)[0] > 9999) return;
      count += 1;
      yield c;
      if (r.count !== null && count >= r.count) return;
    }
  }
}

function optLimit(limit) {
  if (limit == null) return null;
  needInt(limit, 'limit');
  if (limit < 1) fail(E_RANGE, 'limit must be positive');
  return limit;
}

/**
 * Expand a rule into dates.
 *
 * The occurrences are computed from `dtstart` as described above (COUNT
 * always counts from dtstart, regardless of the window). Then the window is
 * applied: `after` and `before` are **inclusive** bounds by default;
 * `{ inclusive: false }` makes both exclusive. `limit` caps the number of
 * dates returned (counted after the window).
 *
 * A rule without COUNT and UNTIL must be bounded by `before` or `limit`,
 * otherwise `E_LIMIT` is thrown (before anything is computed).
 *
 * Argument checks, in order: rule, dtstart, after, before, limit (integer ≥ 1).
 *
 * @param {string|object} rule
 * @param {string} dtstart
 * @param {object} [options]
 * @param {string|null} [options.after=null]
 * @param {string|null} [options.before=null]
 * @param {number|null} [options.limit=null]
 * @param {boolean} [options.inclusive=true]
 * @returns {string[]}
 * @example expandRRule('FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH;COUNT=4', '2024-03-06')
 *   // ['2024-03-07', '2024-03-18', '2024-03-21', '2024-04-01']
 * @example expandRRule('FREQ=MONTHLY', '2024-01-31', { limit: 3 })
 *   // ['2024-01-31', '2024-03-31', '2024-05-31']
 */
function expandRRule(rule, dtstart, { after = null, before = null, limit = null, inclusive = true } = {}) {
  const r = toRule(rule);
  const start = toDays(dtstart, 'dtstart');
  const lo = after == null ? null : toDays(after, 'after');
  const hi = before == null ? null : toDays(before, 'before');
  limit = optLimit(limit);
  if (r.count === null && r.until === null && hi === null && limit === null) {
    fail(E_LIMIT, 'unbounded rule needs COUNT, UNTIL, before or limit');
  }
  const out = [];
  for (const c of iterate(r, start, hi)) {
    if (hi !== null && (c > hi || (!inclusive && c === hi))) break;
    if (lo !== null && (c < lo || (!inclusive && c === lo))) continue;
    out.push(iso(c));
    if (limit !== null && out.length >= limit) break;
  }
  return out;
}

/**
 * First occurrence strictly after `after`, or null when there is none
 * (finite rule exhausted, or nothing found within MAX_PERIODS periods).
 *
 * @param {string|object} rule
 * @param {string} dtstart
 * @param {string} after
 * @returns {string|null}
 */
function nextOccurrence(rule, dtstart, after) {
  const r = toRule(rule);
  const start = toDays(dtstart, 'dtstart');
  const lo = toDays(after, 'after');
  for (const c of iterate(r, start)) if (c > lo) return iso(c);
  return null;
}

/**
 * Is `date` one of the occurrences?
 *
 * @param {string|object} rule
 * @param {string} dtstart
 * @param {string} date
 * @returns {boolean}
 */
function occursOn(rule, dtstart, date) {
  const r = toRule(rule);
  const start = toDays(dtstart, 'dtstart');
  const target = toDays(date);
  for (const c of iterate(r, start, target)) {
    if (c === target) return true;
    if (c > target) return false;
  }
  return false;
}

/**
 * Number of occurrences, optionally only those on or before `before`.
 * Unbounded rules need `before` (`E_LIMIT` otherwise).
 *
 * @param {string|object} rule
 * @param {string} dtstart
 * @param {object} [options]
 * @param {string|null} [options.before=null] inclusive
 * @returns {number}
 */
function countOccurrences(rule, dtstart, { before = null } = {}) {
  const r = toRule(rule);
  const start = toDays(dtstart, 'dtstart');
  const hi = before == null ? null : toDays(before, 'before');
  if (r.count === null && r.until === null && hi === null) {
    fail(E_LIMIT, 'unbounded rule needs COUNT, UNTIL or before');
  }
  let total = 0;
  for (const c of iterate(r, start, hi)) {
    if (hi !== null && c > hi) break;
    total += 1;
  }
  return total;
}

module.exports = {
  FREQS,
  Rule,
  toRule,
  iterate,
  parseRRule,
  stringifyRRule,
  expandRRule,
  nextOccurrence,
  occursOn,
  countOccurrences,
};
