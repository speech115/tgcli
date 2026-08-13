## 2026-08-13 — Validate alias charset on load; ban `@` in session stem (T25)

**Did:** promoted `commands/login.py`'s private `_ALIAS_RE`/
`_validate_new_alias` into `config.validate_alias`, reused by both
`login.py` (unchanged behavior) and now `load_config` for every
`[accounts.<alias>]` key. Added `config.validate_session_charset` to
reject `@` in a `session` stem loaded from config (collides with the
`<stem>@<role>` role-suffix separator `session.py`/`list_roles` rely on).
ADR-0100 (ADR-lite).
**Decided:** full lane — config-schema validation change (ADR-0073
trigger 3). `session`'s charset check stays narrow (only `@`) rather than
alias-grade; the broader path-escape set is T07/ADR-0098's scope.
**Learned:** this overlaps T07 (session-path escape), which independently
excludes `@` from `session` for its own reason; T07's ADR already named
this ticket as the alias-charset owner. Both branches are self-contained
and tested on their own; whichever merges second reconciles the resulting
duplicate `@` check.
**Next:** full gate, push, independent whole-diff review before merge.
