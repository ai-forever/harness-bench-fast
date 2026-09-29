'use strict';

/**
 * chronorule — calendar rules, production calendars and recurrences.
 *
 * This is the public entry point: everything exported here is the supported
 * API, everything else (internal.js, the `Cal` and `Rule` classes, helpers
 * exported from individual modules for tests) may change at any time.
 *
 *     const cr = require('chronorule');
 *     cr.addBusinessDays('2024-05-08', 1, { holidays: ['2024-05-09', '2024-05-10'] });
 *     // → '2024-05-13'
 */

const { ChronoError } = require('./errors');
const civil = require('./civil');
const parse = require('./parse');
const weeks = require('./weeks');
const nth = require('./nth');
const calendar = require('./calendar');
const business = require('./business');
const roll = require('./roll');
const duration = require('./duration');
const period = require('./period');
const rrule = require('./rrule');
const schedule = require('./schedule');
const format = require('./format');
const humanize = require('./humanize');

module.exports = {
  ChronoError,

  // civil.js
  isLeapYear: civil.isLeapYear,
  daysInMonth: civil.daysInMonth,
  daysInYear: civil.daysInYear,
  isValidDate: civil.isValidDate,
  makeDate: civil.makeDate,
  dateParts: civil.dateParts,
  weekday: civil.weekday,
  weekdayCode: civil.weekdayCode,
  dayOfYear: civil.dayOfYear,
  addDays: civil.addDays,
  addMonths: civil.addMonths,
  addMonthsClamped: civil.addMonthsClamped,
  addYears: civil.addYears,
  diffDays: civil.diffDays,
  compareDates: civil.compareDates,
  startOfMonth: civil.startOfMonth,
  endOfMonth: civil.endOfMonth,
  startOfQuarter: civil.startOfQuarter,
  endOfQuarter: civil.endOfQuarter,
  startOfYear: civil.startOfYear,
  endOfYear: civil.endOfYear,
  startOfWeek: civil.startOfWeek,
  endOfWeek: civil.endOfWeek,
  clampDate: civil.clampDate,
  minDate: civil.minDate,
  maxDate: civil.maxDate,
  sortDates: civil.sortDates,
  eachDay: civil.eachDay,
  isSameMonth: civil.isSameMonth,
  isWeekend: civil.isWeekend,
  toCompact: civil.toCompact,

  // parse.js
  parseDate: parse.parseDate,
  tryParseDate: parse.tryParseDate,

  // weeks.js
  isoWeek: weeks.isoWeek,
  isoWeeksInYear: weeks.isoWeeksInYear,
  isoWeekStart: weeks.isoWeekStart,
  isoWeekLabel: weeks.isoWeekLabel,
  weekOfMonth: weeks.weekOfMonth,
  weekOfYear: weeks.weekOfYear,
  weeksInMonth: weeks.weeksInMonth,

  // nth.js
  nthWeekdayOfMonth: nth.nthWeekdayOfMonth,
  lastWeekdayOfMonth: nth.lastWeekdayOfMonth,
  weekdayOccurrence: nth.weekdayOccurrence,
  nextWeekday: nth.nextWeekday,
  prevWeekday: nth.prevWeekday,
  weekdaysInMonth: nth.weekdaysInMonth,
  isLastWeekdayOfMonth: nth.isLastWeekdayOfMonth,

  // calendar.js
  makeCalendar: calendar.makeCalendar,
  dayKind: calendar.dayKind,
  isWorkday: calendar.isWorkday,
  isHoliday: calendar.isHoliday,
  workHours: calendar.workHours,
  dayInfo: calendar.dayInfo,
  holidaysBetween: calendar.holidaysBetween,
  mergeCalendars: calendar.mergeCalendars,

  // business.js
  addBusinessDays: business.addBusinessDays,
  businessDaysBetween: business.businessDaysBetween,
  nextBusinessDay: business.nextBusinessDay,
  prevBusinessDay: business.prevBusinessDay,
  nthBusinessDay: business.nthBusinessDay,
  lastBusinessDay: business.lastBusinessDay,
  businessDaysInMonth: business.businessDaysInMonth,
  workHoursInMonth: business.workHoursInMonth,
  workHoursBetween: business.workHoursBetween,
  businessDaysList: business.businessDaysList,

  // roll.js
  roll: roll.roll,
  rollMany: roll.rollMany,
  isRolled: roll.isRolled,

  // duration.js
  parseDuration: duration.parseDuration,
  formatDuration: duration.formatDuration,
  addDuration: duration.addDuration,
  subtractDuration: duration.subtractDuration,
  negateDuration: duration.negateDuration,
  durationDays: duration.durationDays,
  diffDates: duration.diffDates,
  monthsBetween: duration.monthsBetween,
  ageOn: duration.ageOn,
  endOfMonthAfter: duration.endOfMonthAfter,

  // period.js
  quarterOf: period.quarterOf,
  halfOf: period.halfOf,
  fiscalYear: period.fiscalYear,
  periodOf: period.periodOf,
  periodRange: period.periodRange,
  periodKind: period.periodKind,
  shiftPeriod: period.shiftPeriod,
  periodsBetween: period.periodsBetween,
  periodContains: period.periodContains,
  daysInPeriod: period.daysInPeriod,

  // rrule.js — see README "Имена" for the Python names of these three
  parseRRule: rrule.parseRRule,
  stringifyRRule: rrule.stringifyRRule,
  expandRRule: rrule.expandRRule,
  nextOccurrence: rrule.nextOccurrence,
  occursOn: rrule.occursOn,
  countOccurrences: rrule.countOccurrences,

  // schedule.js
  scheduleBetween: schedule.scheduleBetween,
  scheduleTake: schedule.scheduleTake,
  scheduleNext: schedule.scheduleNext,

  // format.js
  formatDate: format.formatDate,
  formatRange: format.formatRange,
  pluralRu: format.pluralRu,
  monthName: format.monthName,
  weekdayName: format.weekdayName,

  // humanize.js
  describeRule: humanize.describeRule,
  describeDuration: humanize.describeDuration,
  describePeriod: humanize.describePeriod,
  describeDate: humanize.describeDate,
  describeRelative: humanize.describeRelative,
  describeDayRange: humanize.describeDayRange,
};
