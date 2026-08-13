## 2026-08-13 — Media download never publishes incomplete bytes (Composer)

**Did:** ADR-0091: serial size check + fsync-before-checkpoint; striped
short-stream unlink+raise; parallel maps to PolicyError. CONTRACT §3.
Tests in `test_commands_media` / `test_transfer`. Thermos T03 / #207.

**Decided:** Completeness guards now; T31 resumable routing later.

**Learned:** Sparse `truncate(size)` made `st_size` a false completeness
signal — must count downloaded bytes.

**Next:** T31 optional follow-up; continue thermos backlog.
