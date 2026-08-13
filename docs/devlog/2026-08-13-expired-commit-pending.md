## 2026-08-13 — Expired commit not sticky-pending (Composer)

**Did:** `begin_commit` validates TTL before rename to `.pending`; expired
pending renamed back for default cleanup. Tests in `test_safety` /
`test_commands_store`. Thermos T13.

**Decided:** Small-fix restoring preview TTL intent; no ADR.

**Learned:** none.

**Next:** Continue thermos backlog.
