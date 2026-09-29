'use strict';

// Usage: node test/run.js [filter]
const fs = require('fs');
const path = require('path');
const { tests } = require('./harness');

for (const file of fs.readdirSync(__dirname).sort()) {
  if (file.endsWith('.test.js')) require(path.join(__dirname, file));
}

const filter = process.argv[2] || '';
let failed = 0;
let passed = 0;
for (const t of tests) {
  if (filter && !t.name.includes(filter)) continue;
  try {
    t.fn();
    passed += 1;
  } catch (err) {
    failed += 1;
    console.log(`FAIL ${t.name}\n  ${String(err.message).split('\n').join('\n  ')}`);
  }
}
console.log(`${passed} passed, ${failed} failed`);
process.exitCode = failed ? 1 : 0;
