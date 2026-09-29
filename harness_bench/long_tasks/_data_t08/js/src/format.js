'use strict';

/**
 * @module format
 *
 * Formatting dates as Russian text.
 *
 * ## Pattern tokens of {@link formatDate}
 *
 * The pattern is scanned left to right. At each position the **longest**
 * token that starts there wins, in the priority order of the table (which is
 * sorted by length). Characters that do not start a token are copied as
 * they are. Text in square brackets is copied literally without the
 * brackets (`[г.]` → `г.`); an opening bracket without a closing one is an
 * ordinary character.
 *
 * | token  | output                                   | example (2024-03-05) |
 * |--------|------------------------------------------|----------------------|
 * | `YYYY` | year, 4 digits                           | `2024`               |
 * | `GGGG` | ISO week-year, 4 digits                  | `2024`               |
 * | `MMMM` | month name, genitive                     | `марта`              |
 * | `LLLL` | month name, nominative                   | `март`               |
 * | `dddd` | weekday name                             | `вторник`            |
 * | `DDDD` | day of year, 3 digits                    | `065`                |
 * | `MMM`  | month abbreviation (May is `мая`)        | `мар`                |
 * | `YY`   | year, last 2 digits                      | `24`                 |
 * | `MM`   | month, 2 digits                          | `03`                 |
 * | `DD`   | day of month, 2 digits                   | `05`                 |
 * | `dd`   | weekday abbreviation                     | `вт`                 |
 * | `WW`   | ISO week, 2 digits                       | `10`                 |
 * | `M`    | month                                    | `3`                  |
 * | `D`    | day of month                             | `5`                  |
 * | `E`    | ISO weekday number, Monday = 1           | `2`                  |
 * | `Q`    | quarter                                  | `1`                  |
 * | `W`    | ISO week                                 | `10`                 |
 *
 * Note what is *not* a token: `DDD` is read as `DD` followed by `D`
 * (`'055'` for 5 March), `ddd` as `dd` + `d` (and a lone `d` is literal), `Y`
 * alone is literal, and `m`/`y` in lower case are literal. Lower-case `dd`
 * vs upper-case `DD` matters.
 */

const { daysFromCivil, needInt, needMonth, needStr, toDays, wd, weekdayIndex, ymd, pad, div } = require('./internal');
const { fail, E_RANGE } = require('./errors');
const {
  MONTHS_GENITIVE,
  MONTHS_NOMINATIVE,
  MONTHS_PREPOSITIONAL,
  MONTHS_SHORT,
  WEEKDAYS_ACCUSATIVE,
  WEEKDAYS_DATIVE_PLURAL,
  WEEKDAYS_FULL,
  WEEKDAYS_SHORT,
  pluralForm,
} = require('./locale-ru');
const { isoWeekOf } = require('./weeks');

/** Tokens in matching priority. */
const TOKENS = Object.freeze([
  'YYYY', 'GGGG', 'MMMM', 'LLLL', 'dddd', 'DDDD', 'MMM', 'YY', 'MM', 'DD', 'dd', 'WW',
  'M', 'D', 'E', 'Q', 'W',
]);

/** Render one token for day number n. */
function renderToken(tok, n) {
  const [y, m, d] = ymd(n);
  switch (tok) {
    case 'YYYY': return pad(y, 4);
    case 'GGGG': return pad(isoWeekOf(n)[0], 4);
    case 'MMMM': return MONTHS_GENITIVE[m - 1];
    case 'LLLL': return MONTHS_NOMINATIVE[m - 1];
    case 'dddd': return WEEKDAYS_FULL[wd(n)];
    case 'DDDD': return pad(n - daysFromCivil(y, 1, 1) + 1, 3);
    case 'MMM': return MONTHS_SHORT[m - 1];
    case 'YY': return pad(y % 100, 2);
    case 'MM': return pad(m, 2);
    case 'DD': return pad(d, 2);
    case 'dd': return WEEKDAYS_SHORT[wd(n)];
    case 'WW': return pad(isoWeekOf(n)[1], 2);
    case 'M': return String(m);
    case 'D': return String(d);
    case 'E': return String(wd(n) + 1);
    case 'Q': return String(div(m - 1, 3) + 1);
    default: return String(isoWeekOf(n)[1]);
  }
}

/**
 * Format a date with a pattern (see the token table in the module comment).
 *
 * @param {string} date
 * @param {string} pattern
 * @returns {string}
 * @example formatDate('2024-03-05', 'D MMMM YYYY [г.]')   // '5 марта 2024 г.'
 * @example formatDate('2024-03-05', 'dd, DD.MM.YY')       // 'вт, 05.03.24'
 * @example formatDate('2021-01-03', 'GGGG-[W]WW-E')       // '2020-W53-7'
 */
function formatDate(date, pattern) {
  const n = toDays(date);
  needStr(pattern, 'pattern');
  const out = [];
  let i = 0;
  while (i < pattern.length) {
    const ch = pattern[i];
    if (ch === '[') {
      const j = pattern.indexOf(']', i + 1);
      if (j < 0) {
        out.push(ch);
        i += 1;
        continue;
      }
      out.push(pattern.slice(i + 1, j));
      i = j + 1;
      continue;
    }
    const tok = TOKENS.find((t) => pattern.startsWith(t, i));
    if (tok) {
      out.push(renderToken(tok, n));
      i += tok.length;
    } else {
      out.push(ch);
      i += 1;
    }
  }
  return out.join('');
}

/**
 * Human date range, compressing the shared parts:
 *
 * - same day: `5 марта 2024`
 * - same month: `5–9 марта 2024` (en dash U+2013, no spaces)
 * - same year: `28 февраля – 3 марта 2024` (en dash with spaces)
 * - otherwise: `30 декабря 2024 – 2 января 2025`
 *
 * No "г." suffix is added.
 *
 * @param {string} start
 * @param {string} end
 * @returns {string}
 * @throws {ChronoError} E_RANGE when end is before start
 */
function formatRange(start, end) {
  const a = toDays(start);
  const b = toDays(end);
  if (b < a) fail(E_RANGE, 'end before start');
  const [ya, ma, da] = ymd(a);
  const [yb, mb, db] = ymd(b);
  if (a === b) return `${da} ${MONTHS_GENITIVE[ma - 1]} ${ya}`;
  if (ya === yb && ma === mb) return `${da}–${db} ${MONTHS_GENITIVE[mb - 1]} ${yb}`;
  if (ya === yb) return `${da} ${MONTHS_GENITIVE[ma - 1]} – ${db} ${MONTHS_GENITIVE[mb - 1]} ${yb}`;
  return `${da} ${MONTHS_GENITIVE[ma - 1]} ${ya} – ${db} ${MONTHS_GENITIVE[mb - 1]} ${yb}`;
}

/**
 * Pick the Russian plural form for `n` (sign ignored): `one` for 1, 21,
 * 101...; `few` for 2-4, 22-24...; `many` for 0, 5-20, 25-30, 111-114...
 *
 * @param {number} n integer
 * @param {string} one
 * @param {string} few
 * @param {string} many
 * @returns {string} just the word, without the number
 * @example pluralRu(21, 'день', 'дня', 'дней') // 'день'
 * @example pluralRu(12, 'день', 'дня', 'дней') // 'дней'
 */
function pluralRu(n, one, few, many) {
  needInt(n, 'n');
  for (const w of [one, few, many]) needStr(w, 'word form');
  return [one, few, many][pluralForm(n)];
}

const MONTH_FORMS = {
  nominative: MONTHS_NOMINATIVE,
  genitive: MONTHS_GENITIVE,
  prepositional: MONTHS_PREPOSITIONAL,
  short: MONTHS_SHORT,
};
const WEEKDAY_FORMS = {
  full: WEEKDAYS_FULL,
  short: WEEKDAYS_SHORT,
  accusative: WEEKDAYS_ACCUSATIVE,
  dative_plural: WEEKDAYS_DATIVE_PLURAL,
};

/**
 * Month name in a grammatical form: `nominative` (default), `genitive`,
 * `prepositional` or `short`. Form names are plain strings and are not
 * renamed by ports.
 *
 * @param {number} month 1..12
 * @param {object} [options]
 * @param {string} [options.form='nominative']
 * @returns {string}
 * @throws {ChronoError} E_RANGE for an unknown form
 */
function monthName(month, { form = 'nominative' } = {}) {
  needMonth(month);
  needStr(form, 'form');
  if (!Object.prototype.hasOwnProperty.call(MONTH_FORMS, form)) fail(E_RANGE, 'unknown form');
  return MONTH_FORMS[form][month - 1];
}

/**
 * Weekday name in a form: `full` (default), `short`, `accusative` or
 * `dative_plural` (with an underscore, as stored in templates).
 *
 * @param {string} weekday code
 * @param {object} [options]
 * @param {string} [options.form='full']
 * @returns {string}
 * @example weekdayName('WE', { form: 'accusative' }) // 'среду'
 */
function weekdayName(weekday, { form = 'full' } = {}) {
  const w = weekdayIndex(weekday);
  needStr(form, 'form');
  if (!Object.prototype.hasOwnProperty.call(WEEKDAY_FORMS, form)) fail(E_RANGE, 'unknown form');
  return WEEKDAY_FORMS[form][w];
}

module.exports = { TOKENS, formatDate, formatRange, pluralRu, monthName, weekdayName };
