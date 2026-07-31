## 2026-07-31 — Archive Phase 3 review fix 2 (no silent event loss)

**Did:** closed Fable Important on PR #112. `archive sync` now applies
**every** difference event before advancing the changes cursor;
`--max-events` budgets catch-up message fetches only. Peer-less private
deletes exclude `-100…` channel peers. Light reconcile rotates via
`next_offset`. CONTRACT/guide/SKILL + regressions updated.

**Decided:** option 1 from the review — apply-all is correct because local
SQLite apply is free once events are already downloaded.

**Next:** re-gate / re-review PR #112, then merge.
