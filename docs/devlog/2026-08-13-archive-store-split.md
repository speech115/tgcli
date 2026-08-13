## 2026-08-13 — Archive store split + peer identity seam (T30)

**Did:** Split `archive/store.py` (1073 → facade 96) into `schema.py`,
`messages.py`, `transcripts.py`, `sync_state.py`, and `peers.py`. Explore/
search now use `peers.resolve` and shared COALESCE SQL fragments. Facade
re-exports keep existing `store` imports working. ADR-0116, MAP, ceilings,
and a focused peers regression landed. Full gate green.

**Decided:** ADR-0116 — persistence modules + one identity read seam; no
ingest rewrite; no CONTRACT/version change.

**Learned:** Busy-timeout tests patched `store.sqlite3`; after the facade
they must patch `schema.sqlite3` where `connect` lives.

**Next:** Independent whole-diff Spec + Standards review before merge.
