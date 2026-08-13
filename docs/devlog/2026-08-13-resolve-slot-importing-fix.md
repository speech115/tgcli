## 2026-08-13 — resolve_slot legacy .importing fix (Wave D review)

**Did:** Wave D whole-diff review found `resolve_slot` ignored legacy
`.json.importing` markers, orphaning state after a crash mid-migration.
Added reproducing test and `_slot_markers_present` helper; ratcheted
`clone/state.py` ceiling 478→485.

**Decided:** Small-fix lane; no CONTRACT/release change. Mirror-fix for
ADR-0105 crash safety on the ADR-0103 migration path.

**Next:** Re-run Wave D review verdict; remaining debt T28–T37 owner-gated.
