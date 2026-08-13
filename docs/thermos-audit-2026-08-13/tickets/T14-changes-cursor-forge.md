---
id: T14
priority: P2
labels: [bug, needs-triage]
slice: Read path
title: "Bind or refuse forged tg changes --cursor channel sets"
---

## Problem

`changes_cursor` accepts any structurally valid `v1:…` blob with no
signature / binding to `--init` or `--peer`. A forged cursor can
subscribe channels without `--peer` and replay history as `message_new`
via low pts, or silently drop events via high pts.

## Expected

Owner decision: HMAC/bind cursor to account+init, refuse channel maps
not established by `--peer`/`--init`, or document intentional trust of
`--cursor` as operator-controlled. Prefer fail-closed for agent-fed
cursors.

## Evidence

- `src/tgcli/changes_cursor.py`, `commands/changes.py`
- CONTRACT §12 / ADR-0063
- Thermos Wave 7 security Medium#1

## Acceptance

1. Owner picks policy in ticket comment / ADR if CONTRACT changes.
2. Tests lock the chosen policy.
3. Full gate green.
