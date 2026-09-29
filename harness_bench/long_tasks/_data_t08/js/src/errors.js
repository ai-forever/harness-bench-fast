'use strict';

/**
 * @module errors
 *
 * Every failure inside chronorule is reported with a {@link ChronoError}.
 * Callers are expected to branch on the stable `code` property, never on the
 * human readable message: messages are for logs and may change between minor
 * versions without notice.
 *
 * Codes
 * -----
 *
 * | code      | meaning                                                          |
 * |-----------|------------------------------------------------------------------|
 * | `E_PARSE` | a string could not be understood at all (wrong shape / syntax)   |
 * | `E_RANGE` | the shape was fine but a value is out of range (30 Feb, month 13) |
 * | `E_RULE`  | any problem inside a recurrence rule text (see rrule.js)         |
 * | `E_LIMIT` | an operation would not terminate or would produce too much data  |
 * | `E_TYPE`  | an argument has the wrong JavaScript type or an unknown key      |
 *
 * The distinction between `E_PARSE` and `E_RANGE` matters for the import
 * pipeline of the planning service: `E_PARSE` rows are sent back to the user,
 * `E_RANGE` rows are logged as data-quality incidents. Please keep it.
 *
 * Note that recurrence rules deliberately collapse every problem into
 * `E_RULE` (including an impossible UNTIL date such as `20230230`), because
 * the rule editor in the UI only has a single red marker.
 */

/** Syntax error: the input string does not have any recognised shape. */
const E_PARSE = 'E_PARSE';
/** The input has a valid shape but a component is out of range. */
const E_RANGE = 'E_RANGE';
/** Anything wrong with a recurrence rule. */
const E_RULE = 'E_RULE';
/** A computation was refused because it would be unbounded or too large. */
const E_LIMIT = 'E_LIMIT';
/** Wrong argument type, missing required key or unknown option key. */
const E_TYPE = 'E_TYPE';

const CODES = Object.freeze([E_PARSE, E_RANGE, E_RULE, E_LIMIT, E_TYPE]);

/**
 * Error thrown by every chronorule function.
 *
 * `ChronoError` extends the built-in `Error`, so `instanceof Error` keeps
 * working in old call sites. The `message` property is prefixed with the code
 * (`"E_RANGE: month out of range"`) which makes grepping logs easier.
 *
 * @example
 *   try {
 *     parseDate('31.02.2024');
 *   } catch (err) {
 *     if (err instanceof ChronoError && err.code === 'E_RANGE') { ... }
 *   }
 */
class ChronoError extends Error {
  /**
   * @param {string} code one of {@link CODES}
   * @param {string} [message] free text for humans
   */
  constructor(code, message) {
    super(message ? `${code}: ${message}` : code);
    this.name = 'ChronoError';
    /** @type {string} */
    this.code = code;
    /** @type {string} the message without the code prefix */
    this.detail = message || '';
  }
}

/**
 * Throw a {@link ChronoError}. Used as `fail(E_RANGE, '...')` everywhere so
 * that the call sites read like assertions.
 *
 * @param {string} code
 * @param {string} [message]
 * @returns {never}
 */
function fail(code, message) {
  throw new ChronoError(code, message);
}

module.exports = { ChronoError, fail, CODES, E_PARSE, E_RANGE, E_RULE, E_LIMIT, E_TYPE };
