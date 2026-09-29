'use strict';

/**
 * @module humanize
 *
 * Russian descriptions for the UI and for notification e-mails. Every
 * string is produced by fixed rules — no locale data from the platform — so
 * the output is stable and tested character by character. Spaces, commas,
 * "ё" and the en dash matter.
 *
 * ## describeRule(rule, dtstart)
 *
 * The description is assembled from pieces in this order:
 *
 * 1. **Head** from FREQ and INTERVAL:
 *    - INTERVAL 1: `каждый день`, `каждую неделю`, `каждый месяц`, `каждый год`;
 *    - otherwise the plural category of INTERVAL decides:
 *      "one" (21, 31, 101, ...) → `каждый 21 день`, `каждую 21 неделю`,
 *      `каждый 21 месяц`, `каждый 21 год`;
 *      "few" → `каждые 2 дня / недели / месяца / года`;
 *      "many" → `каждые 5 дней / недель / месяцев / лет`.
 *
 * 2. **Days**:
 *    - WEEKLY: ` по ` + weekdays in the dative plural, ordered Monday→Sunday,
 *      joined Russian-style (`по понедельникам, средам и пятницам`). Without
 *      BYDAY the weekday of dtstart is used.
 *    - other frequencies:
 *      - plain BYDAY codes (no ordinal): ` по ` + dative plural, Monday→Sunday;
 *      - BYDAY codes with ordinals: ` в ` + the items **in rule order**, each
 *        as "ordinal + weekday in the accusative": `первый понедельник`,
 *        `последнюю пятницу`, `предпоследнее воскресенье`. Ordinals 1..5, -1
 *        and -2 are words (gender follows the weekday: понедельник, вторник,
 *        четверг are masculine, среда, пятница, суббота feminine, воскресенье
 *        neuter); other positive ordinals are digits with `-й`/`-ю`/`-е`
 *        (`20-й понедельник`), other negative ones the same plus ` с конца`
 *        after the weekday (`3-ю пятницу с конца`). For YEARLY without
 *        BYMONTH the list is followed by ` года`;
 *      - BYMONTHDAY: ` ` + items joined Russian-style + ` числа`, items being
 *        `15-го`, `последнего` (for -1) or `2-го с конца` (other negatives);
 *      - neither BYDAY nor BYMONTHDAY: MONTHLY, and YEARLY with BYMONTH, add
 *        ` N-го числа` with dtstart's day; YEARLY without BYMONTH adds
 *        ` 5 марта` (dtstart's day and month in the genitive); DAILY adds
 *        nothing.
 *
 * 3. **Months**: with BYMONTH, ` в ` + months in the prepositional case,
 *    ascending (`в марте, июне и сентябре`). This applies to every
 *    frequency, WEEKLY and DAILY included.
 * 4. **BYSETPOS**: ` (позиции 1, -1)` — positions in rule order, joined with
 *    `, ` only.
 * 5. **COUNT**: `, 5 раз` / `, 2 раза` / `, 21 раз`.
 * 6. **UNTIL**: `, до 31 декабря 2024 г.`
 *
 * WKST is never mentioned.
 */

const { needMonth, toDays, wd, ymd } = require('./internal');
const { toDuration } = require('./duration');
const { fail, E_RANGE } = require('./errors');
const {
  GENDER_INDEX,
  MONTHS_GENITIVE,
  MONTHS_NOMINATIVE,
  MONTHS_PREPOSITIONAL,
  ORDINAL_SUFFIX,
  ORDINALS,
  ROMAN_QUARTERS,
  UNITS,
  WEEKDAY_GENDER,
  WEEKDAYS_ACCUSATIVE,
  WEEKDAYS_DATIVE_PLURAL,
  WEEKDAYS_FULL,
  joinRu,
  pluralForm,
} = require('./locale-ru');
const { parseLabel } = require('./period');
const { toRule } = require('./rrule');

/** [unit key, gender of the unit noun, phrase for INTERVAL=1] per FREQ. */
const FREQ_UNIT = {
  DAILY: ['day', 'm', 'каждый день'],
  WEEKLY: ['week_acc', 'f', 'каждую неделю'],
  MONTHLY: ['month', 'm', 'каждый месяц'],
  YEARLY: ['year', 'm', 'каждый год'],
};

/** "5 дней", "1 неделю", ... */
function countWords(n, unit) {
  return `${n} ${UNITS[unit][pluralForm(n)]}`;
}

function head(freq, interval) {
  const [unit, gender, single] = FREQ_UNIT[freq];
  if (interval === 1) return single;
  const form = pluralForm(interval);
  const word = UNITS[unit][form];
  if (form === 0) return `${gender === 'f' ? 'каждую' : 'каждый'} ${interval} ${word}`;
  return `каждые ${interval} ${word}`;
}

function ordinalPhrase(n, w) {
  const gender = WEEKDAY_GENDER[w];
  const words = ORDINALS[String(n)];
  if (words) return `${words[GENDER_INDEX[gender]]} ${WEEKDAYS_ACCUSATIVE[w]}`;
  if (n > 0) return `${n}${ORDINAL_SUFFIX[gender]} ${WEEKDAYS_ACCUSATIVE[w]}`;
  return `${-n}${ORDINAL_SUFFIX[gender]} ${WEEKDAYS_ACCUSATIVE[w]} с конца`;
}

function monthdayPhrase(v) {
  if (v > 0) return `${v}-го`;
  if (v === -1) return 'последнего';
  return `${-v}-го с конца`;
}

/** "5 марта 2024 г." for a day number. */
function dateWords(n) {
  const [y, m, d] = ymd(n);
  return `${d} ${MONTHS_GENITIVE[m - 1]} ${y} г.`;
}

function sortedWeekdays(pairs) {
  return Array.from(new Set(pairs.map(([, w]) => w))).sort((a, b) => a - b);
}

/**
 * Describe a recurrence rule in Russian (rules in the module comment).
 *
 * @param {string|object} rule
 * @param {string} dtstart
 * @returns {string}
 * @example describeRule('FREQ=WEEKLY;INTERVAL=2;BYDAY=FR,MO', '2024-01-01')
 *   // 'каждые 2 недели по понедельникам и пятницам'
 * @example describeRule('FREQ=MONTHLY;BYDAY=-1FR;COUNT=12', '2024-01-01')
 *   // 'каждый месяц в последнюю пятницу, 12 раз'
 * @example describeRule('FREQ=YEARLY', '2024-03-08') // 'каждый год 8 марта'
 */
function describeRule(rule, dtstart) {
  const r = toRule(rule);
  const start = toDays(dtstart, 'dtstart');
  const [, m0, d0] = ymd(start);
  const parts = [head(r.freq, r.interval)];
  if (r.freq === 'WEEKLY') {
    const days = r.days.length ? sortedWeekdays(r.days) : [wd(start)];
    parts.push(` по ${joinRu(days.map((w) => WEEKDAYS_DATIVE_PLURAL[w]))}`);
  } else {
    const plain = sortedWeekdays(r.days.filter(([n]) => n === 0));
    const ordinal = r.days.filter(([n]) => n !== 0);
    if (plain.length) parts.push(` по ${joinRu(plain.map((w) => WEEKDAYS_DATIVE_PLURAL[w]))}`);
    if (ordinal.length) {
      let text = ` в ${joinRu(ordinal.map(([n, w]) => ordinalPhrase(n, w)))}`;
      if (r.freq === 'YEARLY' && !r.bymonth.length) text += ' года';
      parts.push(text);
    }
    if (r.bymonthday.length) parts.push(` ${joinRu(r.bymonthday.map(monthdayPhrase))} числа`);
    if (!r.days.length && !r.bymonthday.length) {
      if (r.freq === 'MONTHLY' || (r.freq === 'YEARLY' && r.bymonth.length)) parts.push(` ${d0}-го числа`);
      else if (r.freq === 'YEARLY') parts.push(` ${d0} ${MONTHS_GENITIVE[m0 - 1]}`);
    }
  }
  if (r.bymonth.length) parts.push(` в ${joinRu(r.bymonth.map((m) => MONTHS_PREPOSITIONAL[m - 1]))}`);
  if (r.bysetpos.length) parts.push(` (позиции ${r.bysetpos.join(', ')})`);
  if (r.count !== null) parts.push(`, ${countWords(r.count, 'time')}`);
  if (r.until !== null) parts.push(`, до ${dateWords(r.until)}`);
  return parts.join('');
}

/**
 * Describe a duration: non-zero components in the order years, months,
 * weeks, days, separated by single spaces (`1 год 2 месяца 3 недели 1 день`);
 * `0 дней` for a zero duration; negative ones are prefixed with `минус `.
 * Weeks use the nominative (`1 неделя`).
 *
 * @param {string|object} value
 * @returns {string}
 */
function describeDuration(value) {
  const dur = toDuration(value);
  const words = [];
  for (const [name, unit] of [['years', 'year'], ['months', 'month'], ['weeks', 'week'], ['days', 'day']]) {
    if (dur[name]) words.push(countWords(dur[name], unit));
  }
  if (!words.length) return '0 дней';
  const text = words.join(' ');
  return dur.sign < 0 ? `минус ${text}` : text;
}

/**
 * Describe a period label:
 *
 * - `2024` → `2024 год`
 * - `2024-H1` → `1-е полугодие 2024 г.`
 * - `2024-Q3` → `III квартал 2024 г.`
 * - `2024-05` → `май 2024 г.`
 * - `2024-W05` → `5-я неделя 2024 г.`
 * - `FY2025` → `2025 финансовый год`, and when `fiscalStart` is not 1 the
 *   start is added: `2025 финансовый год (с 1 июля 2024 г.)`.
 *
 * @param {string} label
 * @param {object} [options]
 * @param {number} [options.fiscalStart=1]
 * @returns {string}
 */
function describePeriod(label, { fiscalStart = 1 } = {}) {
  const [kind, y, x] = parseLabel(label);
  needMonth(fiscalStart);
  if (kind === 'year') return `${y} год`;
  if (kind === 'half') return `${x}-е полугодие ${y} г.`;
  if (kind === 'quarter') return `${ROMAN_QUARTERS[x - 1]} квартал ${y} г.`;
  if (kind === 'month') return `${MONTHS_NOMINATIVE[x - 1]} ${y} г.`;
  if (kind === 'week') return `${x}-я неделя ${y} г.`;
  let text = `${y} финансовый год`;
  if (fiscalStart !== 1) text += ` (с 1 ${MONTHS_GENITIVE[fiscalStart - 1]} ${y - 1} г.)`;
  return text;
}

/**
 * `вторник, 5 марта 2024 г.`
 * @param {string} date
 * @returns {string}
 */
function describeDate(date) {
  const n = toDays(date);
  return `${WEEKDAYS_FULL[wd(n)]}, ${dateWords(n)}`;
}

/**
 * Relative wording of `date` as seen from `base`:
 *
 * - difference 0, ±1, ±2: `сегодня`, `завтра`, `послезавтра`, `вчера`,
 *   `позавчера`;
 * - otherwise, when the absolute difference is a multiple of 7 **and less
 *   than 60 days**, in weeks (accusative: `через 1 неделю`, `через 3 недели`,
 *   `5 недель назад`);
 * - otherwise in days (`через 3 дня`, `21 день назад`, `через 63 дня`).
 *
 * @param {string} date
 * @param {string} base
 * @returns {string}
 */
function describeRelative(date, base) {
  const diff = toDays(date) - toDays(base);
  const special = { 0: 'сегодня', 1: 'завтра', 2: 'послезавтра', '-1': 'вчера', '-2': 'позавчера' };
  if (Object.prototype.hasOwnProperty.call(special, String(diff))) return special[String(diff)];
  const k = Math.abs(diff);
  const amount = k % 7 === 0 && k < 60 ? countWords(k / 7, 'week_acc') : countWords(k, 'day');
  return diff > 0 ? `через ${amount}` : `${amount} назад`;
}

/**
 * `3 дня: с 5 марта 2024 г. по 7 марта 2024 г.` — the count includes both
 * ends.
 *
 * @param {string} start
 * @param {string} end
 * @returns {string}
 * @throws {ChronoError} E_RANGE when end is before start
 */
function describeDayRange(start, end) {
  const a = toDays(start);
  const b = toDays(end);
  if (b < a) fail(E_RANGE, 'end before start');
  return `${countWords(b - a + 1, 'day')}: с ${dateWords(a)} по ${dateWords(b)}`;
}

module.exports = {
  describeRule,
  describeDuration,
  describePeriod,
  describeDate,
  describeRelative,
  describeDayRange,
};
