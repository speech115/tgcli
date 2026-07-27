## 2026-07-27 — Named session roles (ADR-0062) (Cursor Grok)

**Did:** implemented ADR-0062 on `cursor/session-roles-cc3b`: role-name
validation (`primary` reserved), `session_path(account, role)`,
`session.client(..., role=)` with no implicit fallback, global
`--session-role`, lifecycle via `accounts login|show|remove --role` and
`doctor` role checks, audit/journal `role` attribution. CONTRACT §1/§5.1/§9/§10,
MAP, guide/accounts, SECURITY, SKILL updated. Gate green:
`1483 passed, 9 skipped`. Live acceptance deferred to owner.

**Decided:** missing/unauthorized role stays `ConfigError` exit 3 (same
taxonomy as an unauthorized primary), matching CONTRACT §4 rather than the
plan's informal "exit 2".

**Learned:** docs gate treats value-taking globals specially — `--session-role`
had to join `--account`/`--timeout` in `scripts/check-docs.py` so `job` is not
misread as a command.

**Next:** stack `tg changes` (ADR-0063) on this branch head; owner live-
accepts both before integrator tags.
