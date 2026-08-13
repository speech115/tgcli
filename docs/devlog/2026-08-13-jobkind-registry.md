## 2026-08-13 — JobKind registry and shared lane loop (ADR-0114 / T33) (Composer)

**Did:** Added `JobKind` registry in `jobs/model.py`; extracted
`_run_lane_loop`, `_run_lane_loop_async`, `_defer_for_cooldown`, and
`_finish_from_result` in `jobs/runner.py`. Preflight reads lane from the
registry. Tests in `tests/test_jobs_model.py`.

**Decided:** ADR-0114; behavior-preserving refactor only — no CONTRACT
change.

**Learned:** Telegram halt-after-progress must preserve the original
`stop_reason or "idle"` fallback when `last_result` carries no
`stop_reason`.

**Next:** Gate + push branch `cursor/jobkind-registry-a379`.
