# ADR-0098: Reject path-escaping `session` stems

Date: 2026-08-13
Status: accepted

## Context

Thermos audit ticket T07
(`docs/thermos-audit-2026-08-13/tickets/T07-session-path-escape.md`, Wave 2
security Medium#1): `Account.session` is an arbitrary string taken from
`config.toml` and joined straight onto
`state_dir()/sessions/<session>.session` by `session_path()`. A value such
as `session = "../../etc/cron.d/evil"` (or an absolute path) resolves the
session and lock files outside `TGCLI_STATE_DIR/sessions/`, so the 0700
directory / 0600 file protections that guard every other account's
`.session` file (full access to a Telegram account) no longer apply to it.

This is a config-schema change — it rejects config values `load_config`
previously accepted — and it protects the state-directory permission model
that ADR-0004 and the session-file access-control rules depend on, so it
takes the full change lane (ADR-0073 triggers 2 and 3) rather than the
small-fix lane.

## Decision

`config.load_config` rejects a `session` stem containing `/`, `\`, `..`, a
null byte, or a leading `-`, or containing `@` (reserved for the ADR-0062
role-suffix separator; the broader alias/session charset ticket is T25) or
that is empty. The check runs on every account before its `Account` is
built — including when `session` is left out and defaults to the alias —
so a config that used to construct a mis-rooted session file now fails
`load_config` itself with a `ConfigError` (exit 3), naming the offending
alias.

`session.session_path` adds a second, independent check as defense in
depth: after joining the stem, it requires
`path.resolve().parent == (state_dir() / "sessions").resolve()`, and raises
`ConfigError` otherwise. `Account` is a plain frozen dataclass that can be
constructed directly (tests already do this), so the path-construction
seam stays safe even for a caller that bypasses `load_config`.

No new dependency, module, or CLI surface. The exit code for a rejected
stem is the existing "config/auth error" (3), already documented for "bad
api_id" in `docs/CONTRACT.md` §4 — this is the same category, not a new
one.

## Rejected alternatives

- **Validate only in `session_path`, not at config load.** A load-time
  rejection gives a clear, immediate `ConfigError` naming the alias before
  any file is touched; deferring it to first use would let `accounts list`
  (which never opens a session) silently accept a config that every other
  command then fails on, and the failure would point at `session_path`
  instead of the config line that caused it.
- **Sanitize the stem instead of rejecting it** (e.g. strip `/`, collapse
  `..`). Silent rewriting means the file `accounts show` reports is not the
  one the operator configured — worse for a security-sensitive path than a
  loud failure.
- **Reuse the alias charset regex (`_ALIAS_RE`) for `session` directly.**
  That charset is alias-grade (letters/digits/`_`/`-`) and bans `-` even
  mid-string and would additionally ban characters that are harmless in a
  filename (e.g. spaces, dots elsewhere). T07 only needs to close the path
  escape; broadening `session`'s charset to alias-grade is T25's scope, not
  this ticket's.

## Contract impact

None. `docs/CONTRACT.md` §4's exit-3 category ("bad api_id") already
covers this; no flag, JSON shape, or exit code changes. `docs/MAP.md`'s
`config.py` line already says "alias + role-name validation" and is
extended to mention the stem check.
