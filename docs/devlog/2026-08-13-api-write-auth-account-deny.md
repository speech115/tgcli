## 2026-08-13 — `tg api --write` wholesale auth/account deny (T04)

**Did:** implemented thermos ticket T04 and ADR-0092 on
`cursor/api-write-auth-deny-1ec8`. `tg api --write` only checked
`HARD_DENYLIST` (four named methods); every other `auth.*`/`account.*`
method (e.g. `auth.importAuthorization`, `account.updateProfile`) passed
through audited. Added `WRITE_NAMESPACE_DENYLIST`/`is_namespace_denied` in
`commands/api.py`, checked alongside `is_hard_denied` in
`preflight._prepare_api`'s write branch — mirrors the read path's own
ADR-0010 wholesale exclusion. Updated CONTRACT §6, `docs/guide/api.md`
(replaced the `account.updateProfile --write` example, expanded the
denylist section), `docs/FEATURES.md` (`account` row now `excluded`, same
as `auth`), MAP, ADR index. Also wrapped two pre-existing E501 lines in
`scripts/publish-thermos-backlog.py` the gate flagged. Full gate: 1891
passed, 9 skipped; ruff, format, architecture, pyright, coverage, docs
green.
**Decided:** kept `HARD_DENYLIST` as a named subset rather than folding it
into the namespace rule — defense in depth if the wholesale exclusion were
ever narrowed for one of the two namespaces. Rejected a per-method
`account.*` allowlist (new feature, needs its own owner request).
**Learned:** `docs/MAP.md`'s ADR range regex only checks the final number
against the actual max ADR file present, not for gaps — `check-docs.py`'s
own test hardcoded "tree ends at 0088" and needed updating alongside the
new ADR-0092.
**Next:** none; T04 closes this slice of the thermos backlog.
