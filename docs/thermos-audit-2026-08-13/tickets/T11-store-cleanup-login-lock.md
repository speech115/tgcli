---
id: T11
priority: P1
labels: [bug, ready-for-agent]
slice: Gates & contract surface
title: "store cleanup must honor login lock without .session"
---

## Problem

`_attempt_lock_held` uses `session.lock_held(session_file)`, which
returns False when the `.session` file does not exist yet.
`unauthorized_client` takes the flock **before** creating SQLite
session. Cleanup can delete `l_*.json` while login is in flight.

## Expected

Probe the `.lock` flock directly. Test: held lock, missing `.session` →
cleanup keeps the attempt.

## Evidence

- `src/tgcli/commands/store.py` (`_attempt_lock_held`)
- `src/tgcli/session.py` (`lock_held`)
- `src/tgcli/authclient.py`
- Thermos Wave 9 security High#2

## Acceptance

1. Regression test for lock-without-session window.
2. Full gate green.
