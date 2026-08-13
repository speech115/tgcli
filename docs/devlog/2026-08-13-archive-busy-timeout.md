## 2026-08-13 — Archive SQLite busy_timeout (Composer)

**Did:** Set `BUSY_TIMEOUT_MS=5000` on `archive.store.connect` (match
jobs/governor). Docstring notes single-writer preference. Test in
`test_archive_store_busy`. Thermos T24.

**Decided:** Small-fix; no ADR.

**Learned:** none.

**Next:** Continue thermos backlog.
