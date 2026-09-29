'use strict';

/**
 * @module parse
 *
 * Lenient parsing of dates typed by people (import files, chat commands,
 * scanned forms). Everything else in the library only accepts strict ISO
 * strings; `parseDate` is the single entry point that turns "whatever the
 * user wrote" into one.
 *
 * ## Accepted shapes
 *
 * Leading and trailing whitespace is removed first (`String#trim`). Then the
 * shapes below are tried **in this order**; the first one whose regular
 * expression matches the whole string decides, even if the date it describes
 * turns out to be invalid (then the result is `E_RANGE`, the remaining shapes
 * are not tried):
 *
 * 1. `YYYY-MM-DD` — ISO, two-digit month and day are mandatory.
 * 2. `YYYYMMDD` — eight digits.
 * 3. `D.M.YYYY` or `D.M.YY` — day first, dots, one or two digits for day and
 *    month. A two-digit year is expanded with a **pivot of 50**:
 *    `00..49 → 2000..2049`, `50..99 → 1950..1999`. Three-digit years are not
 *    accepted.
 * 4. `YYYY/M/D` — year first with slashes.
 * 5. `D/M/YYYY` — slashes with the year last. This one is ambiguous; by
 *    default the first number is the day (Russian habit). Pass
 *    `{ dayFirst: false }` for the American `M/D/YYYY` order. Two-digit years
 *    are *not* accepted with slashes.
 * 6. `D <month word> YYYY` — e.g. `5 марта 2024`, `5 март 2024`,
 *    `05 мар. 2024`, `5 марта 2024 г.`, `5 марта 2024г`. The whole string is
 *    lower-cased before matching, so `5 МАРТА 2024 Г.` works too. The month
 *    word may be followed by one dot. Words are separated by one or more
 *    plain spaces (tabs are not allowed inside). The trailing `г`/`г.` may be
 *    separated by zero or more spaces. The month word must be one of the
 *    words listed in locale-ru.js `MONTH_WORDS`; a word of Cyrillic letters
 *    that is not in the table is `E_PARSE`. Latin month names are not
 *    supported.
 *
 * Anything else is `E_PARSE`. A non-string argument is `E_TYPE`.
 */

const { iso, makeDays, needStr } = require('./internal');
const { ChronoError, fail, E_PARSE } = require('./errors');
const { MONTH_WORDS } = require('./locale-ru');

const RE_ISO = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/;
const RE_COMPACT = /^([0-9]{4})([0-9]{2})([0-9]{2})$/;
const RE_DOTS = /^([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{4}|[0-9]{2})$/;
const RE_YMD_SLASH = /^([0-9]{4})\/([0-9]{1,2})\/([0-9]{1,2})$/;
const RE_DMY_SLASH = /^([0-9]{1,2})\/([0-9]{1,2})\/([0-9]{4})$/;
const RE_TEXT = /^([0-9]{1,2}) +([а-яё]+)\.? +([0-9]{4})(?: *г\.?)?$/;

/** Two-digit years below this value belong to the 21st century. */
const PIVOT = 50;

/**
 * Expand a year written with two or four digits.
 * @param {string} text
 * @returns {number}
 * @example expandYear('49') // 2049
 * @example expandYear('50') // 1950
 */
function expandYear(text) {
  const y = parseInt(text, 10);
  if (text.length === 2) return y < PIVOT ? 2000 + y : 1900 + y;
  return y;
}

function num(s) {
  return parseInt(s, 10);
}

/**
 * Parse a human-written date into `"YYYY-MM-DD"`.
 *
 * @param {string} text
 * @param {object} [options]
 * @param {boolean} [options.dayFirst=true] only affects the `a/b/YYYY` shape
 * @returns {string}
 * @throws {ChronoError} E_TYPE (not a string), E_PARSE (no shape matched or
 *   unknown month word), E_RANGE (shape matched but the date is impossible)
 *
 * @example parseDate('2024-03-05')        // '2024-03-05'
 * @example parseDate('5.3.24')            // '2024-03-05'
 * @example parseDate('05.03.1999')        // '1999-03-05'
 * @example parseDate('03/05/2024', { dayFirst: false }) // '2024-03-05'
 * @example parseDate(' 5 марта 2024 г. ') // '2024-03-05'
 * @example parseDate('31.02.2024')        // throws E_RANGE
 * @example parseDate('2024-3-5')          // throws E_PARSE (ISO needs two digits)
 */
function parseDate(text, { dayFirst = true } = {}) {
  needStr(text, 'text');
  const s = text.trim();
  let mt = RE_ISO.exec(s) || RE_COMPACT.exec(s);
  if (mt) return iso(makeDays(num(mt[1]), num(mt[2]), num(mt[3])));
  mt = RE_DOTS.exec(s);
  if (mt) return iso(makeDays(expandYear(mt[3]), num(mt[2]), num(mt[1])));
  mt = RE_YMD_SLASH.exec(s);
  if (mt) return iso(makeDays(num(mt[1]), num(mt[2]), num(mt[3])));
  mt = RE_DMY_SLASH.exec(s);
  if (mt) {
    const a = num(mt[1]);
    const b = num(mt[2]);
    const [day, month] = dayFirst ? [a, b] : [b, a];
    return iso(makeDays(num(mt[3]), month, day));
  }
  mt = RE_TEXT.exec(s.toLowerCase());
  if (mt) {
    const month = MONTH_WORDS[mt[2]];
    if (month === undefined) fail(E_PARSE, 'unknown month name');
    return iso(makeDays(num(mt[3]), month, num(mt[1])));
  }
  return fail(E_PARSE, 'unrecognised date');
}

/**
 * Like {@link parseDate} but returns `null` instead of throwing a
 * ChronoError (any code, including E_TYPE).
 *
 * @param {*} text
 * @param {object} [options]
 * @param {boolean} [options.dayFirst=true]
 * @returns {string|null}
 */
function tryParseDate(text, { dayFirst = true } = {}) {
  try {
    return parseDate(text, { dayFirst });
  } catch (err) {
    if (err instanceof ChronoError) return null;
    throw err;
  }
}

module.exports = { parseDate, tryParseDate, expandYear, PIVOT };
