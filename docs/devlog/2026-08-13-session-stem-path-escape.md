## 2026-08-13 — T07: reject path-escaping session stems (Claude Sonnet 5)
**Did:** implemented thermos ticket T07 on `cursor/session-path-escape-1ec8`.
`config.validate_session_stem` rejects a `session` value containing `/`,
`\`, `..`, a null byte, a leading `-`, `@`, or empty, called from
`load_config` for every account (including the alias-defaulted case).
`session.session_path` adds an independent resolve-under-`sessions/` check
as defense in depth for an `Account` built without `load_config`. Fixed
`scripts/publish-thermos-backlog.py` E501 (two lines wrapped) so the gate
stays green. Full gate: 1895 passed, 9 skipped; ruff, format,
architecture, pyright, coverage, docs all green.
**Decided:** ADR-0098 (full lane — config schema + the state-directory
safety model). Reserved `@` for the ADR-0062 role separator here rather
than widening `session`'s charset to alias-grade; that broader T25 ticket
stays scoped separately.
**Learned:** a `..` inside a stem with no `/` cannot actually escape the
directory (it is just a filename with dots in it) — the character-set
reject follows the ticket's literal wording anyway, and the resolve-check
in `session_path` is the layer that would actually catch a real escape.
**Next:** T25 (alias charset validation on `load_config`, `@` already
closed here).
