## 2026-07-31 — Archive Phase 5 search and exploration (Codex)

**Did:** implemented the Phase 5 offline archive surface on
`codex/archive-phase5`: filtered FTS5 search with BM25/date ordering, bounded
pages and snippets, offline timeline reads, revision/tombstone history, and
`tg://` handoff links. Added ADR-0069, CLI/parser/preflight wiring, active
contract and guide updates, and permanent boundary tests. Focused archive
coverage is 60 passed; focused pyright is clean; the architecture checker
passes with the existing integrator grace band.

**Decided:** keep SQL query composition and stored-payload rendering in
`archive/explore.py`; preserve `archive/search.py` as the MATCH-normalization
and peer-resolution seam. Search/read/history never open Telegram and remain
available under `--readonly`.

**Learned:** the archive's staleness signal must remain explicit because local
results cover only stored peers; bounded pagination must fetch one extra row so
`has_more` and `next_page` describe the same query window. The new exploration
surface also made the existing command/preflight ceilings visible, so the
shared modules were reduced into the existing grace band without changing
ceiling files.

**Next:** run the full repository gate, then commit and push the feature branch
for independent integration review. Phase 6 remains the separate refresh,
launchd, failure-notification, and release slice; no live-account smoke was
performed in this session.
