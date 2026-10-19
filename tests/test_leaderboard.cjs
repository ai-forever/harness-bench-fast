const assert = require("node:assert/strict");
const fs = require("node:fs");
const { selectResults, formatPassed } = require("../docs/leaderboard.js");
const data = JSON.parse(fs.readFileSync(require("node:path").join(__dirname, "../docs/leaderboard-results.json")));
const light = selectResults(data, "light", "historical", "");
const medium = selectResults(data, "medium", "historical", "");
assert.equal(light.length, 82);
assert.equal(medium.length, 78);
assert.equal(selectResults(data, "medium", "current", "").length, 4);
for (const rows of [light, medium]) {
  for (let i = 1; i < rows.length; i++) assert.ok(rows[i - 1].result.passed >= rows[i].result.passed);
}
assert.equal(light.find(r => r.id === "secret-700-sft").result.passed, 368);
assert.equal(medium.find(r => r.id === "secret-700-sft").result.passed, 9);
assert.equal(selectResults(data, "light", "historical", "  CODEX ").length, 1);
assert.equal(selectResults(data, "light", "historical", "no-such-model").length, 0);
assert.equal(formatPassed(390), "390");
assert.equal(formatPassed(389 + 1 / 3), "389.3");
const missing = { rows: [{ id: "absent", model: "test", harness: "test", light: null }] };
assert.deepEqual(selectResults(missing, "light", "historical", ""), []);
console.log("Leaderboard scope, ranking, missing-data and search checks passed.");

for (const row of data.rows.filter(r => r.source_version === "0.19.0")) { assert.equal(row.light.passed + row.medium.passed, row.source_passed); assert.equal(row.medium.total, 80); }
