# Tasks: add-fork-table-ranks-and-sorting

## Implementation

- [x] Add leading place column (`td.place`) to `_rows_html()` and `_sample_rows_html()` in `scripts/build_fork_home.py`
- [x] Add `#` header cell to `HEAD` and `SAMPLE_HEAD`
- [x] Mark sortable headers with `data-sort` (`number` / `ratio` / `text`)
- [x] Implement `numDataRows()` (continuous numbering across groups) in the page script
- [x] Implement `sortTable()` + `parseVal()` (asc/desc toggle, per-group sorting, `—` last)
- [x] Add sorting styles (`th.sortable` arrows, `td.place`)
- [x] Regenerate `docs/fork.html` and `docs/fork.en.html`

## Spec / Docs

- [x] Write OpenSpec change (proposal.md, design.md, tasks.md, specs/result-reporting/spec.md)
- [x] Reference the Workboard card `ff7939d1-87df-483a-b98e-8462462c1b53` in the proposal

## QA / Verification

- [x] Verification: `#` column shows continuous numbering across `Our runs` / `Upstream comparison`; clicking `%`, `Coverage`, `Time`, `Steps`, `Tokens (cost)` and text headers sorts asc then desc within each group with `—` rows last — verified on the built `docs/fork.html` in headless Chromium (project-a-agent, 2026-11-14)
