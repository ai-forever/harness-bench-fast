"use strict";

// Every view has one explicit denominator. Missing scores never fall back to a
// full-run score, and unmeasured wave 19 is never treated as forty failures.
function selectResults(data, suite, coverage, search) {
  const key = suite === "light" ? "light" : coverage === "current" ? "medium" : coverage === "wave19" ? "wave19" : "medium_historical";
  const query = search.trim().toLocaleLowerCase();
  return data.rows.filter(row => row[key] !== null && row[key] !== undefined)
    .filter(row => (row.model + " " + row.harness).toLocaleLowerCase().includes(query))
    .map(row => ({ ...row, result: row[key], usage: row.telemetry?.[key] || null }))
    .sort((a, b) => b.result.passed / b.result.total - a.result.passed / a.result.total ||
      a.model.localeCompare(b.model) || a.harness.localeCompare(b.harness));
}

function formatPassed(value) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function formatUsage(usage, metric) {
  const item = usage?.[metric];
  const label = metric === "steps" ? "Steps" : "Tokens";
  if (!item || item.status === "unavailable" || !Number.isFinite(item.value)) {
    return { text: "—", note: "Unavailable", detail: label + ": unavailable for this task subset." };
  }
  const text = item.value >= 1000000
    ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(item.value)
    : new Intl.NumberFormat("en-US").format(item.value);
  const coverage = item.covered_tasks + "/" + usage.tasks + " tasks";
  const status = { complete: "Recorded", partial: "Partial", raw: "Raw" }[item.status];
  const note = item.status === "complete" ? "" : status + " · " + coverage;
  const detail = label + ": " + new Intl.NumberFormat("en-US").format(item.value) +
    (item.unit ? " " + item.unit.replaceAll("_", " ") : "") + ". " + status + "; " + coverage + ". " +
    (usage.notes || []).join(" ");
  return { text, note, detail };
}

if (typeof module !== "undefined") module.exports = { selectResults, formatPassed, formatUsage };

if (typeof document !== "undefined") {
  (async function () {
    const body = document.getElementById("results");
    const search = document.getElementById("search");
    const more = document.getElementById("show-more");
    const empty = document.getElementById("empty");
    const buttons = [...document.querySelectorAll("[data-suite]")];
    let suite = location.hash === "#medium" ? "medium" : "light";
    const rowAnchors = { "#secret-700b": "secret-700b-v019", "#secret-sft-mini": "secret-sft-mini-v019" };
    // Preserve incoming links to models and protocol notes from the old landing.
    if (location.hash && !["#light", "#medium", ...Object.keys(rowAnchors)].includes(location.hash)) {
      location.replace("archive.html" + location.hash);
      return;
    }
    let expanded = Object.hasOwn(rowAnchors, location.hash);
    let data;
    function renderTable(target, rows, ranking, scope) {
      target.replaceChildren();
      rows.forEach(row => {
        const tr = document.createElement("tr");
        const anchor = Object.keys(rowAnchors).find(key => rowAnchors[key] === row.id);
        if (anchor && scope === "current") tr.id = anchor.slice(1);
        const rank = document.createElement("td");
        const firstEqual = ranking.findIndex(other => Math.abs(other.result.passed - row.result.passed) < 1e-9);
        rank.textContent = firstEqual + 1;
        const who = document.createElement("td");
        const link = document.createElement("a");
        link.href = row.details_url || (row.source_version === "0.19.0" ? "wave19-measurements.html#" + row.id : "archive.html#measurement-" + row.id);
        link.textContent = row.model;
        const harness = document.createElement("span");
        harness.className = "harness";
        const compositeView = row.measurement_type === "historical_plus_wave19" && suite === "medium" && scope === "current";
        harness.textContent = row.harness + " · " + (compositeView ? "historical + wave 19" : row.runs + (row.runs === 1 ? " run" : " runs")) +
          (suite === "medium" && scope === "wave19" ? " · wave 19 only" : "") +
          (row.result.note ? " · " + row.result.note : "");
        who.append(link, harness);
        const passed = document.createElement("td");
        passed.className = "numeric";
        passed.dataset.label = "Passed";
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
        score.dataset.label = "Score";
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
        for (const metric of ["steps", "tokens"]) {
          const cell = document.createElement("td");
          cell.className = "numeric usage-cell";
          cell.dataset.label = metric === "steps" ? "Steps" : "Tokens";
          const formatted = formatUsage(row.usage, metric);
          const value = document.createElement("span");
          value.className = "usage-value";
          value.textContent = formatted.text;
          value.title = formatted.detail;
          value.setAttribute("aria-label", formatted.detail);
          value.tabIndex = 0;
          cell.append(value);
          if (formatted.note) {
            const note = document.createElement("span");
            note.className = "usage-note";
            note.textContent = formatted.note;
            cell.append(note);
          }
          tr.append(cell);
        }
        target.append(tr);
      });
    }
    function render() {
      buttons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.suite === suite)));
      document.getElementById("suite-title").textContent = "harness_bench_fast " + suite;
      document.getElementById("coverage-note").textContent = suite === "light"
        ? "Tasks 1–391 · complete Light coverage · v0.19.0 measurements and historical v0.18.0 rescores."
        : "Tasks 392–471 · all 80 Medium tasks · historical + wave 19 composites are labeled. New-task results follow below.";
      const rows = selectResults(data, suite, "current", search.value);
      const ranking = selectResults(data, suite, "current", "");
      renderTable(body, expanded ? rows : rows.slice(0, 12), ranking, "current");
      document.getElementById("run-count").textContent = rows.length + (rows.length === 1 ? " configuration" : " configurations");
      more.hidden = expanded || rows.length <= 12;
      more.textContent = "Show all " + rows.length + " results";
      const waveSection = document.getElementById("wave19-section");
      waveSection.hidden = suite !== "medium";
      const waveRows = suite === "medium" ? selectResults(data, "medium", "wave19", search.value) : [];
      if (suite === "medium") {
        renderTable(document.getElementById("wave19-results"), waveRows, selectResults(data, "medium", "wave19", ""), "wave19");
        document.getElementById("wave19-count").textContent = waveRows.length + (waveRows.length === 1 ? " configuration" : " configurations");
        document.getElementById("wave19-empty").hidden = waveRows.length > 0;
      }
      empty.hidden = rows.length > 0;
      empty.textContent = waveRows.length > 0
        ? "No complete 80-task results match this search. New-task results are listed below."
        : "No results match your search.";
    }
    try {
      const response = await fetch("leaderboard-results.json");
      if (!response.ok) throw new Error("HTTP " + response.status);
      data = await response.json();
      render();
      if (Object.hasOwn(rowAnchors, location.hash)) {
        document.getElementById(location.hash.slice(1)).scrollIntoView({ block: "center" });
      }
      buttons.forEach(button => button.addEventListener("click", () => {
        suite = button.dataset.suite;
        history.replaceState(null, "", "#" + suite);
        expanded = false;
        render();
      }));
      window.addEventListener("hashchange", () => {
        suite = location.hash === "#medium" ? "medium" : "light";
        expanded = Object.hasOwn(rowAnchors, location.hash);
        if (expanded) search.value = "";
        render();
        if (expanded) document.getElementById(location.hash.slice(1)).scrollIntoView({ block: "center" });
      });
      search.addEventListener("input", () => { expanded = false; render(); });
      more.addEventListener("click", () => { expanded = true; render(); });
    } catch (error) {
      empty.hidden = false;
      empty.textContent = "Results could not be loaded. Please reload or open the static measurement archive.";
      document.getElementById("coverage-note").textContent = "Measurement data unavailable.";
      console.error(error);
    }
  })();
}
