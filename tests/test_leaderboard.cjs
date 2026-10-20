const assert = require("node:assert/strict");
const fs = require("node:fs");
const { selectResults, formatPassed } = require("../docs/leaderboard.js");
const data = JSON.parse(fs.readFileSync(require("node:path").join(__dirname, "../docs/leaderboard-results.json")));
const light = selectResults(data, "light", "historical", "");
const medium = selectResults(data, "medium", "historical", "");
assert.equal(light.length, 85);
assert.equal(medium.length, 79);
assert.equal(selectResults(data, "medium", "current", "").length, 11);
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

for (const row of data.rows.filter(r => r.measurement_type === "full_run")) { assert.equal(row.light.passed + row.medium.passed, row.source_passed); assert.equal(row.medium.total, 80); }

const wave19 = selectResults(data, "medium", "wave19", "");
assert.equal(wave19.length, 24);
assert.equal(selectResults(data, "light", "current", "GPT-6-Astra (OpenRouter) (medium)").length, 0);
assert.equal(selectResults(data, "medium", "current", "GPT-6-Astra (OpenRouter) (medium)")[0].result.passed, 58);
assert.equal(wave19.find(r => r.id === "astra-pi-medium").result.passed, 23);
assert.equal(selectResults(data, "light", "current", "Sonnet 5.5 (OpenRouter) (medium)").length, 0);
assert.equal(selectResults(data, "medium", "current", "Sonnet 5.5 (OpenRouter) (medium)")[0].result.passed, 53);
assert.equal(wave19.find(r => r.id === "sonnet55-pi-medium").result.passed, 32);
assert.equal(wave19.find(r => r.id === "w19-claude-haiku").result.passed, 26);
assert.equal(selectResults(data, "medium", "current", "Claude Code").length, 0);
assert.equal(selectResults(data, "medium", "wave19", "Claude Code").length, 4);
assert.equal(selectResults(data, "medium", "current", "OpenClaw")[0].result.passed, 41);
for (const row of data.rows.filter(r => r.measurement_type === "historical_plus_wave19")) {
  assert.equal(row.medium.passed, row.medium_historical.passed + row.wave19.passed);
  assert.equal(row.medium.total, 80);
}

const landing = fs.readFileSync(require("node:path").join(__dirname, "../docs/index.html"), "utf8");
assert.ok(!landing.includes("medium-coverage"));
assert.ok(!landing.includes("Task coverage"));
assert.ok(landing.includes('id="wave19-results"'));

const allSecret = selectResults(data, "light", "current", "secret sft");
assert.equal(allSecret.length, 2);
const mini = allSecret.find(row => row.id === "secret-sft-mini-v019");
assert.equal(mini.result.passed, 371);
assert.equal(mini.medium.passed, 8);
assert.equal(mini.wave19.passed, 0);
assert.equal(mini.source_passed, 379);
assert.equal(mini.measurement_valid, true);
assert.equal(mini.details_url, "secret-sft-mini.html");
assert.equal(mini.result.wilson95.length, 2);
const secret = allSecret.filter(row => row.id === "secret-700b-v019");
assert.equal(secret.length, 1);
assert.equal(secret[0].result.passed, 370);
assert.equal(secret[0].measurement_valid, true);
assert.equal(secret[0].result.wilson95, undefined);
assert.equal(selectResults(data, "medium", "current", "secret sft deepagents")[0].result.passed, 7);
assert.equal(selectResults(data, "medium", "wave19", "secret sft")[0].result.passed, 0);

assert.ok(!landing.includes('class="run-snapshot"'));
assert.ok(!landing.includes('secret sft · stopped run'));
assert.equal(secret[0].details_url, "secret-700b.html");
assert.ok(fs.existsSync(require("node:path").join(__dirname, "../docs/secret-700b.html")));

assert.equal(secret[0].validation_basis, "operator_acceptance");
assert.ok(!landing.includes("unvalidated"));
