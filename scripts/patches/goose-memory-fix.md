# Goose 1.54.0 memory fix

Upstream tag: `aaif-goose/goose v1.54.0`.

`developer/tree.rs::count_file_lines` used `fs::read_to_string` on every regular
file. Linux procfs `pagemap` reports zero size but can supply terabytes of data.
`tree("/proc/self", depth=4)` reproduced OOM in the original binary inside
bubblewrap: an open `/proc/2/task/2/pagemap`, 466824 KiB sampled Goose RSS and
`memory.events: oom_kill 1` in a dedicated 512 MiB test cgroup.

Apply `goose-1.54.0-tree-bounded.patch` to the upstream source. It excludes
`/proc`, `/sys`, `/dev` from traversal, opens only regular non-symlink files,
skips zero-size files, bounds reads to initial file size, and counts UTF-8 lines
with a 64 KiB buffer. It preserves invalid-UTF-8 and trailing-newline behavior.

Build the CLI features used by this benchmark:

```sh
cargo build --locked --release -p goose-cli --bin goose \
  --no-default-features --features rustls-tls,tree-sitter,online-model-meta
```

Unused voice, local-inference, AWS, telemetry, desktop and scheduler features
are omitted. Developer tools and the OpenAI provider remain enabled.

Goose also buffers shell output in an unbounded channel before truncation.
Set `GOOSE_SHELL` in the Goose launcher to the absolute executable path of
`scripts/hbf-goose-shell`, and expose that file in the bubblewrap runtime
manifest. This wrapper captures both streams directly to files, preserves exit
status, emits small output verbatim, and returns a bounded tail plus the full
file path for large output. It does not impose task RAM or execution limits.

For the queue unit, explicitly set `LimitNOFILE=65536:524288` and
`OOMPolicy=continue`; verify with `systemctl show`. Run the dispatcher with
`--continue-on-run-failure`: failed runs remain failed and cause a nonzero final
queue exit, but do not prevent other harnesses from starting. Ambiguous launches
still block to prevent duplicate execution. Infrastructure failures must be
repaired/rerun before reporting scores.

If a launcher waits for Goose, translate a negative SIGKILL return code into
exit 137 and write the exact line `HBF_CLI_PROCESS_SIGNAL:9` to stderr. The runner
recognizes that explicit launcher failure (or a directly killed CLI), records it
as infrastructure, and applies its bounded transient retries. Task timeouts keep
their existing normal-failure semantics.

Before deployment, back up results, state, launcher and binary. Replace the
binary atomically; existing processes retain their mapped executable. To resume
only Goose, freeze dispatcher admission briefly, interrupt only its verified
parent, wait for its checkpoint and exit, then remove only its state entry.
Reload the dispatcher from the saved state while preserving other children
(temporarily use `KillMode=process` for the supervisor-only restart, then restore
`KillMode=control-group`). Merely resuming a frozen dispatcher would overwrite
the edited file from its in-memory state. Keep the existing `--rerun-on-fail`
manifest. Verify previously
successful attempts retain their results. Do not restart other harnesses.
