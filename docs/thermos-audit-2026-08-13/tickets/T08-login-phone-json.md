---
id: T08
priority: P1
labels: [bug, ready-for-agent]
slice: Session & auth
title: "Keep full phone out of login attempt JSON (ADR-0088)"
---

## Problem

ADR-0088 says phone numbers are absent from persistent attempt JSON.
`login_state` still stores full `phone` in `logins/l_*.json` (up to TTL).
stdout/audit masking is fine; disk persistence is not.

## Expected

Do not persist full phone. Continue path should use staged Telethon
session / other non-PII handle after `send_code_request`.

## Evidence

- `src/tgcli/login_state.py` (record fields)
- ADR-0088
- Thermos Wave 2 security Medium#2

## Acceptance

1. Test: attempt JSON on disk has no raw phone after start_login.
2. Continue-login still works.
3. Full gate green. Update ADR/CONTRACT if wording needs alignment.
