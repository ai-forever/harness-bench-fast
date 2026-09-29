'use strict';

// Minimal test harness: collects tests, run.js executes them.
const tests = [];

function test(name, fn) {
  tests.push({ name, fn, file: module.parent ? module.parent.filename : '' });
}

module.exports = { test, tests };
