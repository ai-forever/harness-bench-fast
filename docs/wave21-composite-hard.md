# Wave 21: Medium continuation with ten coupled workflows

Task-set 0.21.2 selects **512,518,520,522,523,524,525,526,527,530** as the new
wave. Run it with `--suite composite-hard`. Original task IDs, prompts, domain
rules and service budgets are unchanged.

The release integrates the current trusted host-service runtime from main
(`wave19-host-service-v2-completion`), with host timing and completion receipts.
Historical calibration used immutable snapshots with the earlier host runtime.
Its outcomes are retained under those original protocols; they are not fresh
measurements of the release runtime. Runtime hashes must not be relabeled or
mixed on resume.

The criterion is at most two successful unique model+harness pairs among the
seven configurations tested on all twenty tasks. A pair counts once if it has
at least one normal verified PASS; Sonnet 80/160 are one pair. Diagnostic Codex
runs and verifier-only success after failed termination do not count. Opus was
tested only on 521/522 and is excluded from that denominator; 522 has its extra
Opus witness explicitly recorded. This is adaptive selection on existing
calibration, not an independent test set or a uniform-budget comparison.

| Original ID | Successful fully calibrated pairs | Work |
|---|---:|---|
|512|1|Incident reports and corrected delivery effects|
|518|2|Verified recovery and backup publication|
|520|2|Identity restatement and reference-preserving names|
|522|2|Latency reports and runtime timeout goals|
|523|2|Template graph and compatible package release|
|524|2|Dependent drafts and concurrent source-cut merge|
|525|2|Delivery reconciliation and fresh test evidence|
|526|2|Migrated sources and database return corrections|
|527|2|Live snapshot/WAL and versioned object placement|
|530|2|Inherited configuration and new live deployment goals|

Machine-readable selection, exclusions and provenance: [wave21-selected10.json](wave21-selected10.json).

```bash
uv run python -m harness_bench list --suite composite-hard
uv run python -m harness_bench verify-gold --suite composite-hard
uv run python -m harness_bench run-openrouter --suite composite-hard \
  --model YOUR_MODEL --isolation bwrap --recursion-limit 160 \
  --max-tokens 16384 --forward-reasoning-history --no-subagents
```

The full 531-task registry retains controls for stable IDs and historical
lookup; its `default` and `medium` scores are not the ten-task wave score.

## Original twenty-task calibration, retained separately

Historical development candidate, task-set 0.21.0/0.21.1. Select `--suite composite-hard-calibration` or
IDs 512–531. These are **20 tasks**, with one agent trajectory per pair. The
forty v8b source tasks 472–511 are preserved in the separate `general-hard`
suite; every source is used exactly once in the new set.

The source-to-source exchanges carry current values and versions. Completing
one lane can change the other lane's obligations; completing that peer sends
a correction back. Success requires both current domain goals and all bounded
exchanges. Independent historical scores, or their pairwise AND, do not measure
this execution. No specialist facts or external network access are needed.

| ID | Source tasks | Coupled work |
|---|---|---|
|512|472+486|Incident reports and corrected delivery effects|
|513|473+490|Windowed stream and causal event application|
|514|474+509|Session closeout and related document changes|
|515|475+483|Resource bookings and live traffic routing|
|516|476+503|Shipments/returns and invoice settlement|
|517|477+502|Ledger import and compensating settlement|
|518|478+500|Verified recovery and backup publication|
|519|479+501|Donor failover repair and archive generations|
|520|480+498|Identity restatement and reference-preserving names|
|521|481+504|Privacy deletion coverage and database migration|
|522|482+506|Latency reports and runtime timeout goals|
|523|484+492|Template graph and compatible package release|
|524|485+496|Dependent drafts and concurrent source-cut merge|
|525|487+510|Delivery reconciliation and fresh test evidence|
|526|488+505|Migrated sources and database return corrections|
|527|489+499|Live snapshot/WAL and versioned object placement|
|528|491+495|Changing archive predicate and inherited policy|
|529|493+511|Rollback bridges and dependency-bound acceptance|
|530|494+507|Inherited configuration and new live deployment goals|
|531|497+508|Stable anchors and dependent file edits|

The complete public contract is generated into each workspace's
`docs/taskctl.md`, `left/TASK.md`, `right/TASK.md`, lane documentation and
fixtures. Cross-event formulas, affected identities, finite event counts and
irreversible barriers are explicit. Task 513 clarifies the former ambiguous
window encoding: `[10*k,10*k+10)` has JSON key `str(10*k)`.

## Execution and verification

One trusted host service owns both domain states and the replayable history.
Commands are `python3 tools/taskctl left ...`, `right ...`, and `pair status`;
scoped clients also exist under each lane. File arguments are relative to that
lane, independent of the shell working directory. A child's logical clock
advances only when that child receives a request. Cross-service changes follow
domain milestones, including durable effects with uncertain acknowledgements.

The shared budget is **320 service requests**, including reads, help and
errors, and **900 seconds**. There is no extra 160-call limit per child.
DeepAgents calibration uses **160 graph supersteps**, separately disclosed in
the prompt, and 16,384 output tokens per response. Scripts may loop over public
requests. Fixed inputs, transport and connection files are immutable. Both
the old and composite goal/safety invariants are verified against trusted replay.

Gold clients use public observations only. Admission checks cover all twenty
gold witnesses and blank rejection, alternative valid strategies, independent
parent solutions as negative controls, malformed requests, lane clocks/file
scope, stale domain evidence, unchanged original contracts and Linux isolation.
Passing these checks establishes concrete witnesses, not measured SOTA difficulty.
Model calibration belongs to an immutable source snapshot and is reported
separately; adaptive authoring makes this a development suite, not a held-out test.

```bash
uv run python -m harness_bench list --suite composite-hard-calibration
uv run python -m harness_bench verify-gold --suite composite-hard-calibration
uv run python -m harness_bench run-openrouter --suite composite-hard-calibration \
  --model YOUR_MODEL --isolation bwrap --recursion-limit 160 \
  --max-tokens 16384 --forward-reasoning-history --no-subagents
```

The original v8b Haiku measurement was 29/40 raw, with one contract-ambiguous
failure excluded from model-error attribution. It does not provide a score for
this new twenty-task suite. No near-frontier difficulty claim follows from gold
acceptance or from mechanically combining those historical outcomes.
