---
id: T18
priority: P2
labels: [bug, ready-for-agent]
slice: Clone
title: "Kind-aware clone lookup.matches for status filters"
---

## Problem

`lookup.matches` always tries channel `-100` marking. Dialog/basic
clones can match channel-shaped filters; basic-group `-N` may not match
raw id filters. Status/export and sync disagree on tokens.

## Expected

Kind-aware matching: channel → `-100`, chat → `-N`, user → raw only.
Coordinate with T05 if clone_id migration lands.

## Evidence

- `src/tgcli/clone/lookup.py`
- Thermos Wave 4 security Medium#3

## Acceptance

1. Tests per kind for filter tokens.
2. Full gate green.
