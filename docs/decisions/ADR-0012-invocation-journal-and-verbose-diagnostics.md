# ADR-0012: Local invocation journal and opt-in verbose diagnostics

Status: accepted (2026-07-10)

## Context

The CLI already has structured stdout, stderr, and exit codes, but an operator
cannot inspect past command use, failures, or durations after a short-lived
process exits. The existing `-v` flag was declared but inert.

Long-running-service tracing is outside the stateless CLI architecture, and
recording message texts or raw API parameters would create an unnecessary
privacy risk.

## Decision

- Every successfully parsed command appends one metadata-only JSON object to
  `~/.local/state/tgcli/invocations.jsonl` (or `TGCLI_STATE_DIR`). It records
  timestamp, command, resolved account when applicable, exit code, structured
  error code when applicable, and duration in milliseconds.
- The journal never stores message text, search text, chat references, output,
  or raw API parameters.
- Journal-write failures are fail-open warnings on stderr: observability must
  not change the result of a read or a mutation that has its separate
  fail-closed safety audit.
- `-v` / `--verbose` configures Python and Telethon debug logging on stderr for
  the current CLI process. Stdout remains contract data.

## Consequences

- Local JSONL is enough to investigate use and failure trends without a daemon
  or remote telemetry service.
- `audit.jsonl` remains the authoritative, fail-closed record for mutations;
  `invocations.jsonl` is an operational diagnostic record.
- Operators needing one-off wire-level detail rerun the command with `-v`.
