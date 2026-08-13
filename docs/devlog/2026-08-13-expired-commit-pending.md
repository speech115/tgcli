## 2026-08-13 — Expired commit not sticky-pending (Composer)

**Did:** `begin_commit` validates TTL before rename to `.pending`; expired
pending renamed back for default cleanup. Tests in `test_safety` /
`test_commands_store`. Thermos T13.

**Decided:** Safety behavior (preview→commit) is a full-lane trigger per
AGENTS.md regardless of size; added ADR-0097 rather than treating this as
a plain small-fix.

**Learned:** none.

**Next:** Continue thermos backlog.
