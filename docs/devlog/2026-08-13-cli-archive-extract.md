## 2026-08-13 — Archive CLI extract (thermos T32)

**Did:** Extracted `tg archive` grammar, preflight, and offline routing into
`archive/arguments.py`, `archive/preflight.py`, and `archive/offline.py`
(ADR-0115). Shared `backfill_spec` / `sync_spec` with `jobs/preflight.py`.
`cli.py` 714→665, `parser.py` 726→591, `preflight.py` 342→226 lines.

**Gate:** `./scripts/gate.sh` green.

**Next:** Independent whole-diff review before merge.
