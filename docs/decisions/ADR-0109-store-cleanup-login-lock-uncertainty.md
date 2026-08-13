# ADR-0109: Store cleanup treats login lock uncertainty as busy

Date: 2026-08-13
Status: accepted
Form: ADR-lite (ADR-0058; full lane under ADR-0073)
Extends: [ADR-0040](ADR-0040-wacli-review-adoption-scope.md) cleanup,
[ADR-0042](ADR-0042-accounts-login.md) staged login state, and
[ADR-0043](ADR-0043-process-hardening.md) lock probing.

## Context

`authclient.unauthorized_client` acquires a staged login's flock before
Telethon creates its SQLite `.session`. The released `store cleanup` command
used `session.lock_held`, whose intentional missing-session result is `False`,
and could therefore reap an expired login that was still running. Probing the
`.lock` directly closes that window, but the first implementation also removed
cleanup's existing `OSError` policy: a non-`BlockingIOError` from `flock`
aborted cleanup instead of treating the lock state as unknown. This change is
full-lane because it governs a released cleanup command, persistent login
state, and a new direct lock-file probe.

## Decision

`session.lock_file_held(lock_path)` is the shared direct-flock probe for a
known lock path. `store cleanup` checks that the `.lock` exists before calling
it, so an ordinary inventory does not create lock files.

Cleanup may delete lock-protected state only after a definite free result:
`False`. A held result (`True`), an unopenable result (`None`), or any
`OSError` raised while probing with `flock` all mean busy. Unknown lock state
therefore preserves the affected login attempt or media cache while cleanup
continues instead of deleting live state or aborting the whole command.

`session.lock_held(session_file)` remains the account/doctor seam: a missing
session is `False` and creates no lock file, as required by CONTRACT §5.1.
Regression tests cover a held login lock without a `.session` and a generic
`flock` failure at the public cleanup seam.

## Rejected alternatives

- Treat a missing staged `.session` as unlocked: this is the race that can
  delete an in-flight authorization attempt.
- Make `lock_held` probe a lock beside every missing session: that would weaken
  its side-effect-free account and doctor contract.
- Treat an unprobeable lock as free: cleanup would turn a local probe failure
  into permission to delete persistent state.
- Propagate the probe error: deletion stays avoided, but one uncertain lock
  makes unrelated cleanup work fail instead of conservatively skipping it.

## Contract impact

None. CLI flags, JSON shapes, stdout, and exit codes are unchanged.
`docs/CONTRACT.md`, version files, and `CHANGELOG.md` are untouched.
