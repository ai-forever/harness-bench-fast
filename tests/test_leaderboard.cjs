const assert = require("node:assert/strict");
const fs = require("node:fs");
const { selectResults, selectMedium90, formatComponent, formatPassed, formatUsage } = require("../docs/leaderboard.js");
const data = JSON.parse(fs.readFileSync(require("node:path").join(__dirname, "../docs/leaderboard-results.json")));
const light = selectResults(data, "light", "historical", "");
const medium = selectResults(data, "medium", "historical", "");
assert.equal(light.length, 93);
assert.equal(medium.length, 79);
assert.equal(selectResults(data, "medium", "current", "").length, 19);
for (const rows of [light, medium]) {
  for (let i = 1; i < rows.length; i++) assert.ok(rows[i - 1].result.passed >= rows[i].result.passed);
}
assert.equal(light.find(r => r.id === "secret-700-sft").result.passed, 368);
assert.equal(medium.find(r => r.id === "secret-700-sft").result.passed, 9);
assert.equal(selectResults(data, "light", "historical", "  CODEX ").length, 3);
assert.equal(selectResults(data, "light", "historical", "no-such-model").length, 0);
assert.equal(formatPassed(390), "390");
assert.equal(formatPassed(389 + 1 / 3), "389.3");
const missing = { rows: [{ id: "absent", model: "test", harness: "test", light: null }] };
assert.deepEqual(selectResults(missing, "light", "historical", ""), []);
console.log("Leaderboard scope, ranking, missing-data and search checks passed.");

for (const row of data.rows.filter(r => r.measurement_type === "full_run")) { assert.equal(row.light.passed + row.medium.passed, row.source_passed); assert.equal(row.medium.total, 80); }

const wave19 = selectResults(data, "medium", "wave19", "");
assert.equal(wave19.length, 32);
const ua = selectResults(data, "light", "current", "Universal Agent").filter(r => r.id === "universal-agent-haiku");
assert.equal(ua.length, 1);
assert.equal(ua[0].result.passed, 384);
assert.equal(selectResults(data, "medium", "current", "Universal Agent").find(r => r.id === "universal-agent-haiku").result.passed, 53);
assert.equal(wave19.find(r => r.id === "universal-agent-haiku").result.passed, 32);
assert.equal(ua[0].details_url, "universal-agent-haiku.html");
const uaDeepseek = light.find(row => row.id === "universal-agent-deepseek");
assert.equal(uaDeepseek.result.passed, 389);
assert.equal(uaDeepseek.medium.passed, 55);
assert.equal(uaDeepseek.wave19.passed, 25);
assert.equal(uaDeepseek.source_version, "0.19.0");
assert.equal(uaDeepseek.telemetry.light.tokens.value + uaDeepseek.telemetry.medium.tokens.value, 411778773);
assert.equal(formatUsage(uaDeepseek.telemetry.medium, "tokens").text, "≥ 387.06M");
assert.equal(uaDeepseek.details_url, "universal-agent-deepseek.html");
const ultra = selectResults(data, "light", "current", "Ultra Reasoning").filter(r => r.id === "giga35-ultra-reasoning-v019");
assert.equal(ultra.length, 1);
assert.equal(ultra[0].result.passed, 273);
assert.equal(selectResults(data, "medium", "current", "Ultra Reasoning").find(r => r.id === "giga35-ultra-reasoning-v019").result.passed, 4);
assert.equal(wave19.find(r => r.id === "giga35-ultra-reasoning-v019").result.passed, 1);
assert.equal(ultra[0].details_url, "giga35-ultra-reasoning.html");
assert.equal(ultra[0].source_passed, 277);
assert.equal(ultra[0].measurement_valid, true);
assert.equal(ultra[0].reasoning_effort, "medium");
assert.equal(selectResults(data, "light", "current", "GPT-6-Astra (OpenRouter) (medium)").length, 0);
assert.equal(selectResults(data, "medium", "current", "GPT-6-Astra (OpenRouter) (medium)")[0].result.passed, 58);
assert.equal(wave19.find(r => r.id === "astra-pi-medium").result.passed, 23);
assert.equal(selectResults(data, "light", "current", "Sonnet 5.5 (OpenRouter) (medium)").length, 0);
assert.equal(selectResults(data, "medium", "current", "Sonnet 5.5 (OpenRouter) (medium)")[0].result.passed, 53);
assert.equal(wave19.find(r => r.id === "sonnet55-pi-medium").result.passed, 32);
assert.equal(wave19.find(r => r.id === "w19-claude-haiku").result.passed, 26);
assert.equal(selectResults(data, "medium", "current", "Claude Code").length, 1);
assert.equal(selectResults(data, "medium", "wave19", "Claude Code").length, 5);
assert.equal(selectResults(data, "medium", "current", "OpenClaw")[0].result.passed, 41);
for (const row of data.rows.filter(r => r.measurement_type === "historical_plus_wave19")) {
  assert.equal(row.medium.passed, row.medium_historical.passed + row.wave19.passed);
  assert.equal(row.medium.total, 80);
}

const landing = fs.readFileSync(require("node:path").join(__dirname, "../docs/index.html"), "utf8");
assert.ok(!landing.includes("medium-coverage"));
assert.ok(!landing.includes("Task coverage"));
assert.ok(!landing.includes('id="wave19-results"'));

const allSecret = selectResults(data, "light", "current", "secret sft");
assert.equal(allSecret.length, 3);
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

// Totals follow the selected task subset, never a whole-run fallback.
const astraMedium = selectResults(data, "medium", "current", "GPT-6-Astra")[0];
const astraWave = selectResults(data, "medium", "wave19", "GPT-6-Astra")[0];
assert.equal(astraMedium.usage.steps.value, 1328);
assert.equal(astraMedium.usage.tokens.value, 49216848);
assert.equal(astraWave.usage.steps.value, 283);
assert.equal(astraWave.usage.tokens.value, 4270108);
assert.equal(formatUsage(astraMedium.usage, "tokens").text, "49.22M");
assert.equal(formatUsage(astraMedium.usage, "tokens").note, "Partial · 80/80 tasks");
assert.ok(formatUsage(astraMedium.usage, "tokens").detail.includes("49,216,848"));
assert.equal(formatUsage(astraMedium.usage, "steps").text, "1,328");
assert.equal(formatUsage(null, "tokens").text, "—");
assert.equal(formatUsage({ tasks: 40, steps: { value: null, status: "unavailable" } }, "steps").text, "—");
assert.equal(formatUsage({ tasks: 40, steps: { value: 0, covered_tasks: 40, status: "complete" } }, "steps").text, "0");
assert.equal(formatUsage({ tasks: 80, steps: { value: 17, covered_tasks: 3, status: "raw" } }, "steps").note, "Raw · 3/80 tasks");
assert.equal(selectResults(data, "medium", "current", "").find(r => r.id === "run-19").usage.steps.value, null);
assert.equal(selectResults(data, "light", "current", "Codex").find(r => r.id !== "secret-sft-codex-v019").usage, null);
assert.equal((landing.match(/>Steps<\/th>/g) || []).length, 1);
assert.equal((landing.match(/>Tokens<\/th>/g) || []).length, 1);
console.log("Subset usage, exact totals, incomplete counters and real zero checks passed.");

const codex = selectResults(data, "light", "current", "secret sft Codex")[0];
assert.equal(codex.result.passed, 365);
assert.equal(codex.usage.tokens.value, 8998150);
assert.equal(formatUsage(codex.usage, "tokens").text, "≥ 9M");
assert.equal(selectResults(data, "medium", "current", "secret sft Codex")[0].usage.tokens.value, 103032947);
assert.equal(selectResults(data, "medium", "wave19", "secret sft Codex")[0].usage.tokens.value, 360166);

const brain = data.rows.find(row => row.id === "brain-haiku-experiment");
assert.equal(brain.light.passed + brain.medium.passed, 444);
assert.equal(brain.telemetry.light.tokens.value + brain.telemetry.medium.tokens.value, 191781602);
assert.equal(brain.measurement_valid, false);
assert.match(brain.medium.note, /changed deadline.*transport errors unresolved/);
assert.equal(brain.telemetry.wave19.tasks, 40);
assert.equal(brain.telemetry.wave19.tokens.covered_tasks, 40);
assert.equal(selectResults(data, "medium", "current", "Brain").length, 1);

const haikuRuns = JSON.parse(fs.readFileSync(require("node:path").join(__dirname, "../docs/haiku-three-harnesses-v0191.json")));
assert.equal(haikuRuns.task_set_version, "0.19.1");
assert.equal(haikuRuns.rows.length, 3);
assert.ok(!landing.includes('id="haiku-v0191"'));
for (const row of haikuRuns.rows) {
  assert.equal(row.tasks.length, 471);
  assert.equal(new Set(row.tasks.map(t => t.task_id)).size, 471);
  assert.equal(row.tasks.reduce((n, t) => n + t.tokens, 0), row.tokens);
  assert.equal(row.tasks.filter(t => t.passed).length, row.passed);
  assert.ok(row.model.startsWith("Claude Haiku 5.5"));
}
assert.ok(data.rows.filter(r => /Haiku 5\.5/.test(r.model)).every(r => /Claude Haiku 5\.5/.test(r.model)));

assert.equal(data.task_set_version, "0.19");
for (const id of ["haiku-mini-v019", "haiku-codex-v019", "haiku-claude-v019"]) {
  const row = data.rows.find(row => row.id === id);
  assert.equal(row.source_version, "0.19.1");
  assert.equal(row.display_version, "0.19");
  assert.equal(selectResults(data, "light", "current", "").filter(row => row.id === id).length, 1);
  assert.equal(selectResults(data, "medium", "current", "").filter(row => row.id === id).length, 1);
  assert.equal(row.telemetry.light.tokens.value + row.telemetry.medium.tokens.value,
    haikuRuns.rows.find(source => source.harness === row.harness).tokens);
  assert.equal(row.telemetry.medium.tokens.status, "complete");
}

// Wave 21 within Medium uses its own complete 10-task denominator. Historical and partial
// measurements never fall back into this ranking.
const wave21Fixture = { rows: [
  { id: "old", model: "Fixture", harness: "A", medium: { passed: 80, total: 80 } },
  { id: "partial", model: "Fixture", harness: "B", wave21: { passed: 1, total: 1 } },
  { id: "wave21-80", model: "Sonnet high", harness: "DeepAgents · 80 graph steps", wave21: { passed: 5, total: 10 } },
  { id: "wave21-160", model: "Sonnet high", harness: "DeepAgents · 160 graph steps", wave21: { passed: 9, total: 10 }, telemetry: { wave21: { tasks: 10, steps: { value: 100, covered_tasks: 10, status: "complete" } } } }
] };
const wave21FixtureRows = selectResults(wave21Fixture, "medium", "wave21", "sonnet");
assert.deepEqual(wave21FixtureRows.map(row => row.id), ["wave21-160", "wave21-80"]);
assert.equal(wave21FixtureRows[0].usage.steps.value, 100);
assert.equal(selectResults(wave21Fixture, "medium", "wave21", "no-such-model").length, 0);
assert.ok(!landing.includes('data-suite="hard"'));
assert.equal((landing.match(/data-suite=/g) || []).length, 2);
assert.ok(!landing.includes('id="medium-wave21"'));
assert.ok(!landing.includes('Hard'));
assert.ok(landing.includes('id="coverage-filter"'));
assert.ok(landing.includes('wave21-measurements.html'));
const wave21 = selectResults(data, "medium", "wave21", "");
assert.equal(wave21.length, 8);
assert.ok(wave21.every(row => row.result.total === 10));
assert.deepEqual(wave21.map(row => row.result.passed).sort((a, b) => b - a), [9, 9, 5, 0, 0, 0, 0, 0]);
assert.equal(selectResults(data, "medium", "wave21", "Opus").length, 0);
assert.equal(selectResults(data, "medium", "wave21", "Sonnet").length, 2);
assert.equal(wave21.find(row => row.id === "wave21-sonnet55_80").result.passed, 5);
assert.equal(wave21.find(row => row.id === "wave21-sonnet55_high_160").result.passed, 9);
assert.equal(wave21.find(row => row.id === "wave21-codex0163_deepseek41_unc_high_160").result.passed, 9);
assert.equal(wave21.find(row => row.id === "wave21-qwen38max_high_80").result.passed, 0);


// Medium 90 preserves coverage and never manufactures a full denominator.
const medium90Fixture = { rows: [
  { id: "high-partial", model: "A", harness: "fixture", status: "partial", previous: { passed: 10, measured: 10, total: 80 }, wave21: null, measured_tasks: 10, overall_passed: null },
  { id: "wide-partial", model: "B", harness: "fixture", status: "partial", previous: { passed: 1, measured: 80, total: 80 }, wave21: null, measured_tasks: 80, overall_passed: null },
  { id: "complete", model: "C", harness: "fixture", status: "complete", previous: { passed: 40, measured: 80, total: 80 }, wave21: { passed: 5, measured: 10, total: 10 }, measured_tasks: 90, overall_passed: 45 },
  { id: "opus-partial", model: "Opus", harness: "fixture", status: "partial", previous: null, wave21: { passed: 1, measured: 1, total: 10 }, measured_tasks: 1, overall_passed: null }
] };
assert.deepEqual(selectMedium90(medium90Fixture, "all", "").map(row => row.id), ["complete", "wide-partial", "high-partial", "opus-partial"]);
assert.deepEqual(selectMedium90(medium90Fixture, "complete", "").map(row => row.id), ["complete"]);
assert.deepEqual(selectMedium90(medium90Fixture, "wave21", " OPUS ").map(row => row.id), ["opus-partial"]);
assert.equal(formatComponent(null), "—");
assert.equal(formatComponent({ passed: 0, measured: 0, total: 80 }), "—");
assert.equal(formatComponent({ passed: 9, measured: 40, total: 80 }), "9/40");
assert.equal(formatComponent({ passed: 1, measured: 1, total: 10 }), "1/1");
assert.ok(landing.includes("90 tasks"));
assert.equal((landing.match(/<tbody/g) || []).length, 1);
assert.ok(landing.includes("medium90-results.json"));
console.log("Medium 90 coverage, partial ordering, scope and complete-only ranking checks passed.");
