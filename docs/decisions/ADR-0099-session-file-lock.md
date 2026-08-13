# ADR-0099: Canonical session file lock helper

Date: 2026-08-13
Status: accepted (owner request: thermos audit debt T26 / all-37 campaign)
Form: ADR-lite (new abstraction)

## Context

Non-blocking `fcntl.flock` + busy remediation was copied across
`session.client`, `authclient.unauthorized_client`, `login_state.promote`,
and `accounts` remove/import with inconsistent exception types
(`ConfigError` vs `PolicyError`) and occasional hand-built session paths.
Thermos Wave 2 ranked this as structural debt before the next
login/doctor/accounts change.

## Decision

1. Add `session.session_file_lock(path, *, label=None, busy_error=ConfigError)`
   as the only place that opens the `.lock` beside a session file for
   exclusive non-blocking acquisition.
2. Call sites keep their historical busy exception class via `busy_error`
   (`PolicyError` for `accounts remove`; `ConfigError` elsewhere) — exit
   codes stay unchanged.
3. Prefer `session.session_path(...)` over hand-built `sessions/<stem>`
   paths at those call sites.

## Rejected alternatives

- Force every busy path onto `ConfigError` — would change mutation CLI
  exit semantics for `accounts remove`.
- A heavier `SessionSlot` object owning path+lock+client — YAGNI; the
  lock helper is the seam that stops copy-paste.

## Contract impact

None. Busy messages and exit codes unchanged.
