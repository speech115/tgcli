---
id: T25
priority: P2
labels: [bug, ready-for-agent]
slice: Session & auth
title: "Validate alias charset on load_config; ban @ in session stem"
---

## Problem

Alias regex applies on new login, not on `load_config`. Session stems
with `@` break `list_roles` splitting. Related to T07 path rules.

## Expected

Validate alias charset when loading config. Reject `@` in session stems.
Clear errors, no silent mis-parse of roles.

## Evidence

- `src/tgcli/config.py`, `session.py` (`list_roles`)
- Thermos Wave 2 security Low#3/#6

## Acceptance

1. Tests for invalid alias on load and `@` in session.
2. Full gate green.
