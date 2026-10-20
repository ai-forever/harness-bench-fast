"use strict";

// Every view has one explicit denominator. Missing scores never fall back to a
// full-run score, and unmeasured wave 19 is never treated as forty failures.
function selectResults(data, suite, coverage, search) {
  const key = suite === "light" ? "light" : coverage === "current" ? "medium" : "medium_historical";
  const query = search.trim().toLocaleLowerCase();
  return data.rows.filter(row => row[key] !== null && row[key] !== undefined)
    .filter(row => (row.model + " " + row.harness).toLocaleLowerCase().includes(query))
    .map(row => ({ ...row, result: row[key] }))
    .sort((a, b) => b.result.passed / b.result.total - a.result.passed / a.result.total ||
      a.model.localeCompare(b.model) || a.harness.localeCompare(b.harness));
}

function formatPassed(value) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

if (typeof module !== "undefined") module.exports = { selectResults, formatPassed };

if (typeof document !== "undefined") {
  (async function () {
    const body = document.getElementById("results");
    const search = document.getElementById("search");
    const coverage = document.getElementById("medium-coverage");
    const more = document.getElementById("show-more");
    const empty = document.getElementById("empty");
    const buttons = [...document.querySelectorAll("[data-suite]")];
    let suite = location.hash === "#medium" ? "medium" : "light";
    // Preserve incoming links to models and protocol notes from the old landing.
    if (location.hash && !["#light", "#medium", "#secret-700b"].includes(location.hash)) {
      location.replace("archive.html" + location.hash);
      return;
    }
    let expanded = false;
    let data;
    function render() {
      buttons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.suite === suite)));
      document.getElementById("medium-coverage-label").hidden = suite !== "medium";
      document.getElementById("suite-title").textContent = "harness_bench_fast " + suite;
      document.getElementById("coverage-note").textContent = suite === "light"
        ? "Tasks 1–391 · complete Light coverage · v0.19.0 measurements and historical v0.18.0 rescores."
        : coverage.value === "current"
          ? "Tasks 392–471 · current Medium, 80 tasks · complete wave 19 measurements."
          : "Tasks 392–431 · historical subset: 40 of 80 Medium tasks measured. Wave 19 (432–471) unmeasured; scores below use only these 40 tasks.";
      const rows = selectResults(data, suite, coverage.value, search.value);
      const ranking = selectResults(data, suite, coverage.value, "");
      body.replaceChildren();
      const visible = expanded ? rows : rows.slice(0, 12);
      visible.forEach(row => {
        const tr = document.createElement("tr");
        const rank = document.createElement("td");
        const firstEqual = ranking.findIndex(other => Math.abs(other.result.passed - row.result.passed) < 1e-9);
        rank.textContent = firstEqual + 1;
        const who = document.createElement("td");
        const link = document.createElement("a");
        link.href = row.source_version === "0.19.0" ? "wave19-measurements.html#" + row.id : "archive.html#measurement-" + row.id;
        link.textContent = row.model;
        const harness = document.createElement("span");
        harness.className = "harness";
        harness.textContent = row.harness + " · " + row.runs + (row.runs === 1 ? " run" : " runs");
        who.append(link, harness);
        const passed = document.createElement("td");
        passed.className = "numeric";
        passed.textContent = formatPassed(row.result.passed) + "/" + row.result.total;
        if (row.runs > 1) {
          const average = document.createElement("span");
          average.className = "average-label";
          average.textContent = "mean@" + row.runs;
          average.title = "Average of " + row.runs + " independent runs";
          passed.append(average);
        }
        const score = document.createElement("td");
        score.className = "numeric score-cell";
        const percent = 100 * row.result.passed / row.result.total;
        const scoreLabel = document.createElement("span");
        scoreLabel.textContent = percent.toFixed(1) + "%";
        if (row.result.wilson95) scoreLabel.title = "95% Wilson interval: " + row.result.wilson95.map(x => (100 * x).toFixed(1) + "%").join("–") + "; across tasks, not repeat variability";
        const bar = document.createElement("span");
        bar.className = "score-bar";
        bar.setAttribute("aria-hidden", "true");
        const fill = document.createElement("span");
        fill.style.width = percent + "%";
        bar.append(fill);
        score.append(scoreLabel, bar);
        tr.append(rank, who, passed, score);
        body.append(tr);
      });
      document.getElementById("run-count").textContent = rows.length + " configurations";
      more.hidden = expanded || rows.length <= 12;
      more.textContent = "Show all " + rows.length + " results";
      empty.hidden = rows.length > 0;
      empty.textContent = suite === "medium" && coverage.value === "current"
        ? "No complete 80-task Medium results published. Select the historical 40-task coverage to see existing measurements."
        : "No results match your search.";
    }
    try {
      const response = await fetch("leaderboard-results.json");
      if (!response.ok) throw new Error("HTTP " + response.status);
      data = await response.json();
      render();
      if (location.hash === "#secret-700b") {
        document.getElementById("secret-700b").scrollIntoView();
      }
      buttons.forEach(button => button.addEventListener("click", () => {
        suite = button.dataset.suite;
        history.replaceState(null, "", "#" + suite);
        expanded = false;
        render();
      }));
      window.addEventListener("hashchange", () => {
        suite = location.hash === "#medium" ? "medium" : "light";
        expanded = false;
        render();
      });
      [search, coverage].forEach(input => input.addEventListener("input", () => { expanded = false; render(); }));
      more.addEventListener("click", () => { expanded = true; render(); });
    } catch (error) {
      empty.hidden = false;
      empty.textContent = "Results could not be loaded. Please reload or open the static measurement archive.";
      document.getElementById("coverage-note").textContent = "Measurement data unavailable.";
      console.error(error);
    }
  })();
}
