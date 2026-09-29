'use strict';

/**
 * @module locale-ru
 *
 * Russian word tables used by format.js, parse.js and humanize.js.
 *
 * The library is Russian-only by design (it grew out of the payroll and
 * planning services, which only ever print Russian). Every table is indexed
 * by month number minus one or by weekday number (Monday = 0).
 *
 * Mind the spelling: "четвёртый" is written with "ё" in ordinals, while the
 * weekday name "четверг" has no "ё" at all. Tests compare strings exactly.
 */

/** Nominative month names: "январь", used for stand-alone months ("май 2024 г."). */
const MONTHS_NOMINATIVE = Object.freeze([
  'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
  'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
]);

/** Genitive month names: "5 марта". */
const MONTHS_GENITIVE = Object.freeze([
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]);

/** Prepositional month names: "в марте". */
const MONTHS_PREPOSITIONAL = Object.freeze([
  'январе', 'феврале', 'марте', 'апреле', 'мае', 'июне',
  'июле', 'августе', 'сентябре', 'октябре', 'ноябре', 'декабре',
]);

/**
 * Three-letter abbreviations. Note that May is abbreviated in the genitive
 * ("мая"), as printed on the payroll slips: "5 мая" never gets shortened.
 */
const MONTHS_SHORT = Object.freeze(['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']);

const WEEKDAYS_FULL = Object.freeze(['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']);
const WEEKDAYS_SHORT = Object.freeze(['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']);
/** Accusative singular: "в среду", "в пятницу". */
const WEEKDAYS_ACCUSATIVE = Object.freeze(['понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу', 'воскресенье']);
/** Dative plural: "по средам". */
const WEEKDAYS_DATIVE_PLURAL = Object.freeze([
  'понедельникам', 'вторникам', 'средам', 'четвергам', 'пятницам', 'субботам', 'воскресеньям',
]);
/** Grammatical gender of the weekday nouns: m = masculine, f = feminine, n = neuter. */
const WEEKDAY_GENDER = Object.freeze(['m', 'm', 'f', 'm', 'f', 'f', 'n']);

/**
 * Ordinal adjectives in the accusative, by gender [m, f, n]. Only the ones we
 * spell out in words; other ordinals are written as digits with a suffix
 * (see {@link ORDINAL_SUFFIX}).
 */
const ORDINALS = Object.freeze({
  1: ['первый', 'первую', 'первое'],
  2: ['второй', 'вторую', 'второе'],
  3: ['третий', 'третью', 'третье'],
  4: ['четвёртый', 'четвёртую', 'четвёртое'],
  5: ['пятый', 'пятую', 'пятое'],
  '-1': ['последний', 'последнюю', 'последнее'],
  '-2': ['предпоследний', 'предпоследнюю', 'предпоследнее'],
});

/** Suffix for ordinals written with digits ("20-й понедельник", "7-ю пятницу", "9-е воскресенье"). */
const ORDINAL_SUFFIX = Object.freeze({ m: '-й', f: '-ю', n: '-е' });
const GENDER_INDEX = Object.freeze({ m: 0, f: 1, n: 2 });

const ROMAN_QUARTERS = Object.freeze(['I', 'II', 'III', 'IV']);

/**
 * Month words accepted by `parseDate` (lower case): nominative, genitive and
 * the three-letter abbreviations, plus a few common longer abbreviations
 * ("сент", "февр", "нояб") and "май" (which is both nominative and the
 * natural abbreviation).
 */
const MONTH_WORDS = (() => {
  const words = Object.create(null);
  for (let i = 0; i < 12; i += 1) {
    words[MONTHS_NOMINATIVE[i]] = i + 1;
    words[MONTHS_GENITIVE[i]] = i + 1;
    words[MONTHS_SHORT[i]] = i + 1;
  }
  words['май'] = 5;
  words['сент'] = 9;
  words['февр'] = 2;
  words['нояб'] = 11;
  return Object.freeze(words);
})();

/**
 * Noun forms for counted units, [one, few, many]:
 * "1 день / 2 дня / 5 дней". `week_acc` is the accusative used after
 * "каждую" / "через" ("через 1 неделю").
 */
const UNITS = Object.freeze({
  day: ['день', 'дня', 'дней'],
  week: ['неделя', 'недели', 'недель'],
  week_acc: ['неделю', 'недели', 'недель'],
  month: ['месяц', 'месяца', 'месяцев'],
  year: ['год', 'года', 'лет'],
  time: ['раз', 'раза', 'раз'],
});

/**
 * Russian cardinal plural category of `|n|`:
 * 0 = "one" (1, 21, 101 but not 11), 1 = "few" (2-4, 22-24 but not 12-14),
 * 2 = "many" (everything else, including 0 and 11-14).
 * @param {number} n integer, sign ignored
 * @returns {0|1|2}
 */
function pluralForm(n) {
  n = Math.abs(n);
  if (n % 10 === 1 && n % 100 !== 11) return 0;
  if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return 1;
  return 2;
}

/**
 * Join words the Russian way: "a", "a и b", "a, b и c".
 * @param {string[]} items
 */
function joinRu(items) {
  if (items.length === 0) return '';
  if (items.length === 1) return items[0];
  return `${items.slice(0, -1).join(', ')} и ${items[items.length - 1]}`;
}

module.exports = {
  MONTHS_NOMINATIVE,
  MONTHS_GENITIVE,
  MONTHS_PREPOSITIONAL,
  MONTHS_SHORT,
  WEEKDAYS_FULL,
  WEEKDAYS_SHORT,
  WEEKDAYS_ACCUSATIVE,
  WEEKDAYS_DATIVE_PLURAL,
  WEEKDAY_GENDER,
  ORDINALS,
  ORDINAL_SUFFIX,
  GENDER_INDEX,
  ROMAN_QUARTERS,
  MONTH_WORDS,
  UNITS,
  pluralForm,
  joinRu,
};
