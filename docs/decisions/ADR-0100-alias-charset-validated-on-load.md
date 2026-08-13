# ADR-0100: Validate account-alias charset on every config load

Date: 2026-08-13
Status: accepted
Form: ADR-lite (ADR-0058)

## Context

Thermos audit ticket T25
(`docs/thermos-audit-2026-08-13/tickets/T25-alias-session-validation.md`,
Wave 2 security Low#3/#6): the alias-grade charset regex
(`^[A-Za-z0-9_-]+$`) was enforced in exactly one place —
`commands/login.py`'s `_validate_new_alias`, run only for a brand-new alias
passed to `accounts login`. `config.load_config` never ran it on the
`[accounts.<alias>]` table key it reads from disk, so a config file edited
by hand (or generated) could carry an alias with a space, a dot, or an `@`
and load without complaint; the failure would surface later, confusingly,
wherever that alias was next used as a lookup key, a CLI argument echo, or
part of a session stem.

Separately, `Account.session` (the on-disk stem, defaulting to the alias
but independently overridable via `session = "..."` in config) had no
charset check at all. `session.py` names a role session
`<primary-stem>@<role>` and `list_roles` recovers the role by splitting a
filename on the first `@`; a primary stem that already contains `@` makes
that split misparse part of the stem itself as a role name.

Both are config-schema changes — `load_config` starts rejecting values it
previously accepted — so this takes the full change lane (ADR-0073 trigger
3) rather than the small-fix lane.

This overlaps T07 (`ADR-0098`, session-path escape rejection), which
independently rejects `/`, `\`, `..`, a leading `-`, a null byte, and also
`@` in `session`, for a different reason (escaping `sessions/` and shelling
out to a leading `-`). Both tickets land `@`-in-`session` rejection for
their own stated reason; T07's ADR names this ticket as the owner of the
alias-charset half. Whichever of the two branches merges second will need
to reconcile the resulting duplicate `@` check in `config.py` — expected,
not a defect in either.

## Decision

1. `config.validate_alias(alias)` (promoted out of
   `commands/login.py`'s private `_validate_new_alias`, same message and
   regex) is the one alias-charset check, called from both
   `commands/login.py` (unchanged behavior: only a brand-new alias) and
   now also from `load_config`, for every alias key in `[accounts]`, before
   its entry is even required to be a table. A config with an invalid alias
   fails `load_config` itself with `ConfigError` (exit 3), naming the
   alias — the same "bad api_id"-style category `docs/CONTRACT.md` §4
   already documents, not a new one.
2. `config.validate_session_charset(session, alias)` rejects `@` in a
   `session` stem read from config (explicit or defaulted from the
   already-charset-checked alias), narrowly for the role-suffix collision
   reason above. It is deliberately not the alias-grade charset (spaces or
   dots in a session stem are a filename oddity, not a parser hazard) and
   deliberately not the broader path-escape set T07 owns.

## Rejected alternatives

- **Reuse the alias-grade regex for `session` too.** `session` only needs
  to stay distinguishable from a role suffix; further restricting it would
  reject stems T07 already allows (e.g. a literal `.`) for no seam benefit,
  and duplicates scope T07's ADR explicitly leaves to this ticket.
- **Skip the `@` check here since T07 also adds one.** Neither branch could
  see the other merge first; each staying self-contained means the ticket
  is provably done and tested on its own, and the eventual merge just
  drops one of two equivalent checks.
- **Leave the duplicate `_ALIAS_RE` in `login.py`.** Two copies of the same
  regex is exactly the drift this ticket exists to close; `validate_alias`
  is now the one definition both call sites import.

## Contract impact

None. `docs/CONTRACT.md` §4's exit-3 category already covers a rejected
alias/session value; no flag, JSON shape, or exit code changes.
`docs/MAP.md`'s `config.py` line already says "alias + role-name
validation" and now covers the session-stem charset check too.
