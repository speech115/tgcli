---
id: T07
priority: P1
labels: [bug, ready-for-agent]
slice: Session & auth
title: "Reject path-escaping session stems in config"
---

## Problem

`Account.session` is an arbitrary string. Values with `../` resolve
session and lock files outside `TGCLI_STATE_DIR/sessions/`, weakening
the 0700/0600 state model.

## Expected

Reject stems with `/`, `\`, `..`, leading `-`, `@` (see also T25). After
join, require `session_path(...).resolve()` stays under
`(state_dir()/sessions).resolve()`.

## Evidence

- `src/tgcli/config.py` (session field load)
- `src/tgcli/session.py` (`session_path`)
- Thermos Wave 2 security Medium#1

## Acceptance

1. Tests: escaping stems refused at load or path construction.
2. Full gate green.
