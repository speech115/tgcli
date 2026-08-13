## 2026-08-13 — Session file lock helper (ADR-0099 / T26) (Composer)

**Did:** Added `session.session_file_lock` and routed authclient, login
promote, accounts remove/import, and `session.client` through it. Busy
exception class preserved via `busy_error`. Hand-built remove path now
uses `session_path`. Tests for ConfigError/PolicyError busy + hold.

**Decided:** ADR-0099; number 0099 leaves 0093–0104 for parallel open PRs.

**Learned:** accounts remove busy is PolicyError; client/auth/login busy
is ConfigError — unify the flock, not the exception type.

**Next:** Gate + push; continue remaining thermos debt.
