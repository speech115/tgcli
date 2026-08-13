# ADR-0092: `tg api --write` wholesale-denies `auth.*`/`account.*`

Date: 2026-08-13
Status: accepted (2026-08-13; thermos audit T04, Wave 9 security High#1)

## Context

ADR-0010 excludes `auth.*` and `account.*` from the raw API **read**
allowlist wholesale: "excluded wholesale: credential and account-lifecycle
surface." The **write** path never got the same rule. `_prepare_api`'s
write branch (`src/tgcli/preflight.py`) only checked `HARD_DENYLIST`, four
named methods (`account.deleteAccount`, `auth.logOut`,
`auth.resetAuthorizations`, `account.resetAuthorization`) plus the
destructive-verb `--confirm` gate. Every other `auth.*`/`account.*` method —
`auth.importAuthorization`, `auth.exportAuthorization`,
`account.updateProfile`, `account.updateUsername`, `account.registerDevice`,
and roughly 150 more — passed through `tg api --write` fully audited but
unblocked, contradicting FEATURES.md's `auth` row ("excluded... raw auth
calls are denylisted") and the read path's own posture. `docs/guide/api.md`
even demonstrated `account.updateProfile --write` as the canonical write
example.

## Decision

The write path gains the same wholesale namespace exclusion the read path
already has:

- `src/tgcli/commands/api.py` gains `WRITE_NAMESPACE_DENYLIST =
  frozenset({"account", "auth"})` and `is_namespace_denied(name)`, checked
  against the canonicalized `Namespace.method` string.
- `preflight._prepare_api`'s write branch now raises `PolicyError("raw API
  method is permanently denied")` when `is_hard_denied(method) or
  is_namespace_denied(method)` — before config loading, session
  acquisition, or audit write, matching every other denial in this branch.
- `HARD_DENYLIST` stays as an explicit, named subset: even if the wholesale
  namespace rule were ever narrowed for one of these two namespaces, these
  four specific methods (account deletion, session logout/reset) remain
  denied by name. This mirrors ADR-0010's own layering (a wholesale
  namespace rule plus specific documented exceptions elsewhere).
- No new confirm/config/exemption mechanism: this is a pure narrowing of an
  already-restricted surface, so it needs no destructive-verb interaction
  and cannot regress an existing script (nothing depended on
  `auth.*`/`account.*` writes working, since the guide never told anyone to
  rely on them beyond the one example being replaced).

## Rejected alternatives

- **Expand `HARD_DENYLIST` to name every `auth.*`/`account.*` write
  individually.** Telethon's `account` namespace alone carries ~130
  methods; a per-method list would need to be re-audited every Telethon
  upgrade and drifts from the read path's own wholesale rule. The read
  allowlist already treats "namespace membership proves nothing about
  safety" as the wrong framing for `auth`/`account` specifically (unlike the
  rest of the schema, where per-method review is right) — the write path
  should say the same thing the same way.
- **Add a per-namespace allowlist for `account.*` writes** (e.g. permit
  `updateProfile`/`updateUsername` while blocking lifecycle calls). Rejected
  per ADR-0071/ADR-0073: a new allowlist is a feature, not a fix, and needs
  its own owner request; T04's scope is closing the gap the audit found, not
  designing a new write surface.

## Contract impact

- CONTRACT §6 gains a sentence naming the wholesale `auth.*`/`account.*`
  write exclusion alongside the existing four-method permanent denylist.
- `docs/guide/api.md`'s write example moves off `account.updateProfile`
  (now blocked) onto a non-excluded method; the guide's "Permanent
  denylist" section gains a note that the exclusion is namespace-wide, not
  limited to the four listed methods.
- `docs/FEATURES.md`'s `account` row is corrected: it previously read "Raw
  account calls use the audited write gates," which was false for writes
  once `--write` is requested — it now matches the `auth` row's "excluded"
  wording.
- No exit-code taxonomy change (exit 2 already exists for this branch).
