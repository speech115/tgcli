## 2026-08-13 — Public atomic.fsync_directory (Wave D smell)

**Did:** Renamed `atomic._fsync_directory` to public `fsync_directory` and
pointed `commands/export.py` at it. Regression tests lock the public name
and forbid the private cross-module call.

**Decided:** Small-fix lane; ADR-0108 already required the shared helper.
No CONTRACT change.

**Next:** Continue Wave E debt PRs (T28–T37).
