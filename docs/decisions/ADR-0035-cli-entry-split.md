# ADR-0035: Split the CLI entry monolith; line budgets become ceilings

Date: 2026-07-23
Status: accepted

Builds on: ADR-0026 (maintenance mode), ADR-0034 (shared read-operation seam).

## Context

`src/tgcli/cli.py` held 909 lines across seven functions. Two of them carried
72% of the file: `build_parser()` at 360 lines and `main()` at 295. `main()`
interleaved three unrelated jobs — argument-shape validation, safety gates, and
network dispatch — as one flat `if args.command == ...` chain, so adding a
command meant edits at three or more points inside a single function, in an
order that was not obvious from reading it.

The file was also frozen rather than merely large. `scripts/check-architecture.py`
compared each hotspot's length to its budget with `!=`, and
`test_architecture_check_requires_lowering_a_shrunk_baseline` asserted that
*shrinking* `cli.py` by one line failed the gate too. Every change to the file,
including a one-line fix, required editing the budget constant in the same
commit. A ratchet meant to discourage growth was in practice taxing all work on
the module — and would have blocked this refactor first.

What made the split safe: 304 tests drive `main()` end to end. The observable
behaviour of every command, exit code, and error message was already pinned
from outside.

## Decision

**1. The gate measures ceilings, not baselines.** `check-architecture.py` fails
only when a file exceeds its number. Files may shrink freely; a ceiling is
lowered when a file settles under it. Growth past a ceiling still fails, which
keeps the review checkpoint the ratchet was for.

**2. The CLI entry surface is four modules, one job each.**

- `parser.py` — the argparse subparser tree. Grammar only, no behaviour.
- `preflight.py` — everything that must hold before a session opens: argument
  shape, time-bound parsing, mutation gates, preview loading, raw-API policy.
  Fails closed via `parser.error` (SystemExit) for grammar mistakes and
  `TgcliError` for policy ones.
- `dispatch.py` — network routing for one command under one open session, plus
  translation of Telethon rate-limit errors into the CLI error contract.
- `cli.py` — the invocation lifecycle: parse → preflight → execute → emit →
  journal. It alone emits output and writes the invocation journal.

**3. The read-command ban extends to all four.** ADR-0034 forbids the CLI entry
point from importing read command modules directly; they are reachable only
through `read_ops`. Splitting the file could have laundered that rule, so
`check-architecture.py` now applies the ownership check to `cli.py`,
`parser.py`, `preflight.py`, and `dispatch.py`, not just `cli.py` and
`commands/batch.py`.

**4. Tests patch owning modules, not re-exports.** Tests reached
`tgcli.session` and `tgcli.commands.media` through `cli.session` and
`cli.media_cmd`. Those names no longer exist in `cli.py`; the tests now import
and patch the owning module. `cli._run_network` stays as a thin delegate to
`dispatch.run_network` because it is an established test seam.

## Consequences

- `cli.py`: 909 → 213 lines. New: `parser.py` 375, `preflight.py` 168,
  `dispatch.py` 213. Total 969 lines against the old 909 — the split costs ~60
  lines of module headers and signatures, and buys four files that can each be
  read in one sitting.
- Behaviour is unchanged: 674 tests pass (672 before, plus two new gate tests),
  ruff and pyright clean, exit codes and error strings verified by hand for the
  grammar-error, policy-gate, and success paths.
- Adding a command now touches `parser.py` (grammar), optionally
  `preflight.py` (validation), and `dispatch.py` (routing) — three named
  places instead of three points inside one function.
- Editing `cli.py` no longer requires touching the architecture script unless
  the file actually grows past its ceiling.

## Alternatives rejected

- **Per-command `add_parser`/`validate` in `commands/*.py`.** The natural
  shape, but ADR-0034 forbids the entry point from importing read command
  modules, so read commands could not host their own grammar without either
  breaking that seam or inventing a parallel module tree. Splitting by concern
  rather than by command keeps ADR-0034 intact.
- **Leave `_run_network` in `cli.py`.** Would have avoided all test churn and
  left `cli.py` at ~380 lines, but preserves the 190-line dispatch chain that
  has the same shape problem as `main()`.
- **Rename the budget concept away entirely.** The ceiling still catches
  unreviewed growth, which is the property worth keeping; only the equality
  check was wrong.
