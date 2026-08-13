---
id: T04
priority: P0
labels: [bug, ready-for-agent]
slice: Gates & contract surface
title: "tg api --write: enforce ADR-0010 auth.* / account.* denylist"
---

## Problem

ADR-0010 / FEATURES mark `auth.*` and `account.*` as excluded from raw
API. Read path is default-deny; write path only checks `HARD_DENYLIST`
(four methods) plus destructive `--confirm`. Methods such as
`auth.importAuthorization` remain reachable via audited `--write`.

## Expected

Write path enforces the same wholesale namespace exclusion (or an explicit
expanded denylist). Guide + FEATURES + tests agree. Coverage matrix
should not imply safety it does not enforce (see also T34).

## Evidence

- `src/tgcli/preflight.py` (`_prepare_api` write branch)
- `src/tgcli/commands/api.py` (`HARD_DENYLIST`, `is_hard_denied`)
- ADR-0010, `docs/FEATURES.md`, `docs/guide/api.md`
- Thermos Wave 9 security High#1

## Acceptance

1. Tests: representative `auth.*` / `account.*` writes → exit 2 before
   session/network.
2. CONTRACT/guide text updated if surface changes.
3. Full gate green.
