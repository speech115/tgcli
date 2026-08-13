---
id: T20
priority: P2
labels: [bug, ready-for-agent]
slice: Session & auth
title: "Set flood_sleep_threshold=0 on authclient"
---

## Problem

`session._make_client` sets `flood_sleep_threshold=0` (ADR-0072).
`authclient.unauthorized_client` does not. Login/probe may silently sleep
on short FloodWait instead of surfacing RateLimitError.

## Expected

Same flood_sleep_threshold policy on every TelegramClient constructor.

## Evidence

- `src/tgcli/authclient.py`, `session.py`
- Thermos Wave 2 security Low#4 / quality finding

## Acceptance

1. Test or assertion that unauthorized clients use threshold 0.
2. Full gate green.
