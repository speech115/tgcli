---
id: T26
priority: Debt
labels: [enhancement, needs-triage]
slice: Session & auth
title: "Unify session lock + path API (session_file_lock, SessionSlot)"
---

## Problem

fcntl lock+busy message is copied in ≥6 places with inconsistent
exception types. Session paths are sometimes built by hand instead of
`session_path()`. Quality audit: next login/doctor/accounts change will
add another copy.

## Expected

Canonical `session_file_lock` / promote helpers and a single path/slot
API in `session.py`. Commands call it; no raw flock in accounts/login.

## Evidence

- Thermos Wave 2 quality P1 findings
- `session.py`, `authclient.py`, `login_state.py`, `commands/accounts.py`

## Acceptance

Owner requests slice (+ ADR if new abstraction). Tests for busy/lock
behavior unchanged. Full gate green.
