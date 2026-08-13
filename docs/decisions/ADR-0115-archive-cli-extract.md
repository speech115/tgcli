# ADR-0115: Archive CLI grammar/preflight/offline extraction

Date: 2026-08-13
Status: accepted (owner request: thermos debt T32)

Builds on: ADR-0035 (CLI entry split and ceilings), ADR-0087 (jobs
subsystem extraction pattern).

## Context

`cli.py` and `parser.py` sat on their architecture ceilings. `_execute` had
grown back into an offline router for six archive commands, and archive
validation duplicated the backfill/sync caps already enforced for typed jobs.

## Decision

Mirror the jobs subsystem layout under `archive/`:

- `archive/arguments.py` — the `tg archive` argparse tree;
- `archive/preflight.py` — archive validation plus shared `backfill_spec` /
  `sync_spec` helpers used by `jobs/preflight.py`;
- `archive/offline.py` — local-only archive dispatch (`list`, `status`,
  `search`, `read`, `history`, `transcribe`).

`parser.py`, `preflight.py`, and `cli.py` delegate to these modules. No
CONTRACT change: flags, JSON shapes, and exit codes are unchanged.

## Rejected alternatives

- **Leave archive grammar in `parser.py`.** Preserves the ceiling pressure
  that triggered T32 and keeps archive edits split across three unrelated
  entry modules.
- **Put offline routing in `commands/archive.py`.** Jobs keeps
  `execute_offline` on its command seam; archive offline work is local-store
  I/O and belongs with the other archive-owned CLI modules.

## Contract impact

None.
