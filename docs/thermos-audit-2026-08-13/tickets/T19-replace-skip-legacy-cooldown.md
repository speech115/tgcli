---
id: T19
priority: P2
labels: [bug, ready-for-agent]
slice: Clone
title: "--replace commit must not block on legacy clone cooldown"
---

## Problem

`init --replace --commit` still calls `_enforce_cooldown` on the old
slot before supersede. Legacy `retry_not_before` blocks abandoning the
slot. Account governor should still apply after replace.

## Expected

Skip per-clone legacy cooldown when `replace` is true; keep governor
gates.

## Evidence

- `src/tgcli/commands/clone.py` (replace commit path)
- `src/tgcli/clone/cooldown.py`, `state.py`
- Thermos Wave 4 security Medium#5

## Acceptance

1. Test: cooling legacy slot can be superseded via --replace.
2. Governor refuse still works post-replace.
3. Full gate green.
