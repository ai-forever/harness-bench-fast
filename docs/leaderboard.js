"use strict";

// Every view has one explicit denominator. Missing scores never fall back to a
// full-run score, and unmeasured waves are never treated as failures.
function selectResults(data, suite, coverage, search) {
  const key = suite === "medium" && coverage === "wave21" ? "wave21" : suite === "light" ? "light" : coverage === "current" ? "medium" : coverage === "wave19" ? "wave19" : "medium_historical";
  const query = search.trim().toLocaleLowerCase();
  return data.rows.filter(row => row[key] !== null && row[key] !== undefined)
    .filter(row => key !== "wave21" || row[key].total === 10)
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
  return { text: (item.lower_bound ? "≥ " : "") + text, note, detail };
}

// Partial configurations are ordered by coverage, never by a partial score.
function selectMedium90(data, coverage, search) {
  const query = search.trim().toLocaleLowerCase();
  return data.rows.filter(row => (row.model + " " + row.harness).toLocaleLowerCase().includes(query))
    .filter(row => coverage !== "wave21" || (row.wave21?.measured || 0) > 0)
    .filter(row => coverage !== "complete" || row.status === "complete")
    .sort((a, b) => Number(b.status === "complete") - Number(a.status === "complete") ||
      (a.status === "complete" && b.status === "complete" ? b.overall_passed - a.overall_passed : b.measured_tasks - a.measured_tasks) ||
      a.model.localeCompare(b.model) || a.harness.localeCompare(b.harness));
}

function formatComponent(component) {
  if (!component || !component.measured) return "—";
  return formatPassed(component.passed) + "/" + component.measured;
}

if (typeof module !== "undefined") module.exports = { selectResults, selectMedium90, formatComponent, formatPassed, formatUsage };

if (typeof document !== "undefined") {
  (async function () {
    const body = document.getElementById("results");
    const head = document.getElementById("results-head");
    const search = document.getElementById("search");
    const coverage = document.getElementById("coverage-filter");
    const more = document.getElementById("show-more");
    const empty = document.getElementById("empty");
    const buttons = [...document.querySelectorAll("[data-suite]")];
    const rowAnchors = { "#secret-sft-codex": "secret-sft-codex-v019", "#secret-700b": "secret-700b-v019", "#secret-sft-mini": "secret-sft-mini-v019", "#universal-agent-haiku": "universal-agent-haiku", "#universal-agent-deepseek": "universal-agent-deepseek", "#giga35-ultra-reasoning": "giga35-ultra-reasoning-v019" };
    function selectedSuite() {
      if (["#hard", "#medium-wave21"].includes(location.hash)) history.replaceState(null, "", "#medium");
      return location.hash === "#medium" ? "medium" : "light";
    }
    let suite = selectedSuite();
    if (location.hash && !["#light", "#medium", ...Object.keys(rowAnchors)].includes(location.hash)) {
      location.replace("archive.html" + location.hash);
      return;
    }
    let expanded = Object.hasOwn(rowAnchors, location.hash);
    let data, mediumData;
    function textCell(tr, text, label, className = "numeric") {
      const cell = document.createElement("td");
      cell.className = className;
      cell.dataset.label = label;
      cell.textContent = text;
      tr.append(cell);
      return cell;
    }
    function appendScoreBar(cell, percent) {
      const bar = document.createElement("span");
      bar.className = "score-bar";
      bar.setAttribute("aria-hidden", "true");
      const fill = document.createElement("span");
      fill.style.width = percent + "%";
      bar.append(fill);
      cell.append(bar);
    }
    function componentCell(tr, component, label) {
      const cell = textCell(tr, formatComponent(component), label);
      if (component?.measured) {
        const percent = 100 * component.passed / component.measured;
        cell.title = label + ": " + percent.toFixed(1) + "% of " + component.measured + " measured tasks";
        appendScoreBar(cell, percent);
      }
      return cell;
    }
    function renderTable(rows, ranking) {
      body.replaceChildren();
      rows.forEach(row => {
        const tr = document.createElement("tr");
        const anchor = Object.keys(rowAnchors).find(key => rowAnchors[key] === row.id);
        if (anchor) tr.id = anchor.slice(1);
        const complete = suite === "light" || row.status === "complete";
        const firstEqual = ranking.findIndex(other => suite === "light" ? Math.abs(other.result.passed - row.result.passed) < 1e-9 : other.overall_passed === row.overall_passed);
        textCell(tr, complete ? String(firstEqual + 1) : "—", "Rank", "");
        const who = textCell(tr, "", "Model / harness", "");
        const link = document.createElement("a");
        link.href = row.details_url || (row.source_version === "0.19.0" ? "wave19-measurements.html#" + row.id : "archive.html#measurement-" + row.id);
        link.textContent = row.model;
        const harness = document.createElement("span");
        harness.className = "harness";
        harness.textContent = row.harness + " · " + row.runs + (row.runs === 1 ? " run" : " runs") +
          (suite === "medium" ? " · " + (row.status === "complete" ? "complete" : (row.status === "unverified" || row.measurement_valid === false) ? "unverified measurement" : "partial coverage") : row.result.note ? " · " + row.result.note : "");
        who.append(link, harness);
        if (suite === "medium") {
          const previous = componentCell(tr, row.previous, "Earlier 80");
          if (row.previous?.scope_note) {
            const note = document.createElement("span");
            note.className = "usage-note";
            note.textContent = row.previous.scope_note;
            previous.append(note);
          }
          componentCell(tr, row.wave21, "Wave 21 / 10");
          textCell(tr, row.measured_tasks + "/90", "Coverage");
          textCell(tr, complete ? formatPassed(row.overall_passed) + "/90" : "—", "Overall / 90");
        } else {
          const passed = textCell(tr, formatPassed(row.result.passed) + "/" + row.result.total, "Passed");
          if (row.runs > 1) {
            const average = document.createElement("span");
            average.className = "average-label";
            average.textContent = "mean@" + row.runs;
            average.title = "Average of " + row.runs + " independent runs";
            passed.append(average);
          }
        }
        const score = textCell(tr, "", "Score", "numeric score-cell");
        const percent = suite === "light" ? 100 * row.result.passed / row.result.total : complete ? 100 * row.overall_passed / 90 : null;
        const scoreLabel = document.createElement("span");
        scoreLabel.textContent = percent === null ? "—" : percent.toFixed(1) + "%";
        if (row.result?.wilson95) scoreLabel.title = "95% Wilson interval: " + row.result.wilson95.map(x => (100 * x).toFixed(1) + "%").join("–") + "; across tasks, not repeat variability";
        score.append(scoreLabel);
        if (percent !== null) appendScoreBar(score, percent);
        for (const metric of ["steps", "tokens"]) {
          const cell = textCell(tr, "", metric === "steps" ? "Steps" : "Tokens", "numeric usage-cell");
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
        }
        body.append(tr);
      });
    }
    function render() {
      buttons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.suite === suite)));
      document.getElementById("suite-title").textContent = "harness_bench_fast " + suite;
      document.getElementById("coverage-filter-label").hidden = suite !== "medium";
      document.getElementById("leaderboard").classList.toggle("medium90-table", suite === "medium");
      document.getElementById("coverage-note").textContent = suite === "light"
        ? "391 tasks · frozen Light suite · published measurements retained unchanged."
        : "90 tasks · earlier 80 + wave 21’s selected 10. Component results show passed / measured tasks. Missing coverage is unmeasured; only complete configurations receive a rank and overall score.";
      head.replaceChildren();
      const header = document.createElement("tr");
      const labels = suite === "light" ? ["#", "Model / harness", "Passed", "Score", "Steps", "Tokens"] : ["#", "Model / harness", "Earlier 80", "Wave 21 / 10", "Coverage", "Overall / 90", "Score", "Steps", "Tokens"];
      labels.forEach((label, index) => {
        const th = document.createElement("th");
        th.scope = "col";
        th.textContent = label;
        if (index > 1) th.className = "numeric";
        header.append(th);
      });
      head.append(header);
      const rows = suite === "light" ? selectResults(data, suite, "current", search.value) : selectMedium90(mediumData, coverage.value, search.value);
      const ranking = suite === "light" ? selectResults(data, suite, "current", "") : selectMedium90(mediumData, "complete", "");
      renderTable(expanded ? rows : rows.slice(0, 12), ranking);
      document.getElementById("run-count").textContent = rows.length + (rows.length === 1 ? " configuration" : " configurations") + (suite === "medium" ? " · " + ranking.length + " complete" : "");
      more.hidden = expanded || rows.length <= 12;
      more.textContent = "Show all " + rows.length + " results";
      empty.hidden = rows.length > 0;
      empty.textContent = suite === "medium" && coverage.value === "complete" ? "No complete 90-task configuration has been measured yet." : "No results match your search.";
    }
    try {
      const responses = await Promise.all([fetch("leaderboard-results.json", { cache: "no-cache" }), fetch("medium90-results.json", { cache: "no-cache" })]);
      for (const response of responses) if (!response.ok) throw new Error("HTTP " + response.status);
      [data, mediumData] = await Promise.all(responses.map(response => response.json()));
      render();
      if (Object.hasOwn(rowAnchors, location.hash)) document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: "center" });
      buttons.forEach(button => button.addEventListener("click", () => {
        suite = button.dataset.suite;
        history.replaceState(null, "", "#" + suite);
        expanded = false;
        render();
      }));
      window.addEventListener("hashchange", () => {
        suite = selectedSuite();
        expanded = Object.hasOwn(rowAnchors, location.hash);
        if (expanded) search.value = "";
        render();
        if (expanded) document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: "center" });
      });
      search.addEventListener("input", () => { expanded = false; render(); });
      coverage.addEventListener("change", () => { expanded = false; render(); });
      more.addEventListener("click", () => { expanded = true; render(); });
    } catch (error) {
      empty.hidden = false;
      empty.textContent = "Results could not be loaded. Please reload or open the static measurement archive.";
      document.getElementById("coverage-note").textContent = "Measurement data unavailable.";
      console.error(error);
    }
  })();
}
