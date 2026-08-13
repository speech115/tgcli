## 2026-08-13 — Split jobs store bootstrap (T29)

**Did:** Extracted `jobs/db.py` (schema v1, connect policy, meta bind,
inventory) from the 919-line `jobs/store.py` ceiling file. Store keeps
CRUD, state machine, and lane locks with stable re-exports. Moved runtime
retry policy constants into `jobs/model.py`. ADR-0113, MAP, architecture
ceilings (db 280, store 665, model 55). Added boundary tests in
`test_jobs_store.py`.

**Decided:** Behavior-preserving refactor only — no CONTRACT change.

**Next:** Independent whole-diff review before merge.
