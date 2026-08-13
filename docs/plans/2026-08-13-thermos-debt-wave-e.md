# Thermos debt Wave E — T28–T37 + review smell

Date: 2026-08-13
Owner: sereja (request: «Доделай все» after thermos queue summary)

## Scope

Finish the conscious engineering queue left after Waves A–D landed all
25 thermos bug tickets and 4 of 12 debt tickets (T26, T27, T31, T34).

| PR / branch | Slice | Lane | ADR |
|---|---|---|---|
| `cursor/export-fsync-public-a379` | Public `atomic.fsync_directory` (Wave D minor smell) | small | — |
| `cursor/changes-decompose-a379` | T35: decompose `changes.py` + ceiling | small | — |
| `cursor/clone-send-split-a379` | T28: `clone/send.py` | full (new module) | ADR-0112 |
| `cursor/jobs-store-split-a379` | T29: `jobs/db.py` bootstrap split | full (new module) | ADR-0113 |
| `cursor/jobkind-registry-a379` | T33: JobKind registry + shared lane loop | full | ADR-0114 |
| `cursor/cli-archive-extract-a379` | T32: archive arguments/preflight/offline | full (new modules) | ADR-0115 |
| `cursor/archive-store-split-a379` | T30: split `archive/store.py` + peer identity seam | full | ADR-0116 |
| `cursor/breadth-atomic-a379` | T36: atomic breadth check-and-touch | full (safety) | ADR-0117 |
| `cursor/private-backfill-cursor-a379` | T37: durable private dialog enumeration cursor | full | ADR-0118 |
| `cursor/thermos-debt-wave-e-a379` | Campaign plan + backlog status close-out | docs | — |

## Order and conflict map

Sequential on shared files (`scripts/check-architecture.py`,
`tests/test_check_architecture.py`, `docs/MAP.md`, `docs/decisions/README.md`).
Independent product modules may land in parallel once ADR numbers are
reserved as above.

## Exit criteria

Each code PR: reproducing/behavior tests, full gate green, independent
whole-diff Spec+Standards review before merge. Final docs PR marks T26–T37
done in `docs/thermos-audit-2026-08-13-backlog.md` and updates ISSUES pointer.

## Out of scope

- Product backlog in `docs/ISSUES.md` / `docs/PROPOSALS.md` without owner gate.
- Publishing GitHub issues (token lacks `issues:write`); labels/tags stay in
  the backlog file + ticket frontmatter.
