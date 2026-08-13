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
contract via a thin wrapper. Regression test:
`test_cleanup_keeps_expired_login_lock_held_without_session_file`
(held lock, deleted `.session`, cleanup keeps the record). Also fixed two
pre-existing E501s in `scripts/publish-thermos-backlog.py` (unrelated lint
failures on `main`) so the gate stays green.
**Decided:** small-fix lane (ADR-0073) — this restores the lock's existing
intent (ADR-0004/0062: a held flock means "in use"), not new behavior; no
new ADR.
**Learned:** `_lock_busy` already special-cased "no `.lock` file → not
busy" before calling `lock_held`, which made the bug easy to miss: the
`.lock` file legitimately exists in the buggy window, so the second-order
check (session file existence inside `lock_held`) was the real leak.
**Next:** none — ticket closed.
