## 2026-08-13 — T11: store cleanup honors login lock without `.session` (Cursor)

**Did:** fixed `commands/store.py::_lock_busy`, which delegated to
`session.lock_held(target)`. That helper returns `False` whenever the
target file is missing (CONTRACT §5.1, correct for `accounts show`/`doctor`),
so during the window `authclient.unauthorized_client` holds the staged
`.lock` before Telethon creates the SQLite `.session`, `cleanup` saw "no
`.session`" and reaped an in-flight login. Extracted the flock probe out of
`session.lock_held` into a new `session.lock_file_held(lock_path)` that
probes the `.lock` path directly, independent of any file's existence, and
pointed `_lock_busy` at it. `lock_held` keeps its existing missing-session
contract via a thin wrapper. Regression tests:
`test_cleanup_keeps_expired_login_lock_held_without_session_file`
(held lock, deleted `.session`, cleanup keeps the record) and
`test_cleanup_treats_flock_probe_error_as_busy` (generic `flock` failure,
cleanup preserves the record and continues). Also fixed two pre-existing
E501s in `scripts/publish-thermos-backlog.py` (unrelated lint failures on
`main`).
**Decided:** full lane under [ADR-0112](../decisions/ADR-0112-store-cleanup-login-lock-uncertainty.md):
this changes a released cleanup command, protects persistent login state, and
adds the direct `lock_file_held` abstraction — three ADR-0073 triggers. Only a
definite free probe permits deletion; unknown means busy.
**Learned:** `_lock_busy` already special-cased "no `.lock` file → not
busy" before calling `lock_held`, which made the bug easy to miss: the
`.lock` file legitimately exists in the buggy window, so the second-order
check (session file existence inside `lock_held`) was the real leak. The direct
probe must also preserve cleanup's caller-owned `OSError` policy.
**Next:** independent whole-diff review from `main`.
