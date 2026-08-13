---
id: T06
priority: P1
labels: [bug, ready-for-agent]
slice: Clone
title: "Clone JSON→SQLite import must be crash-safe"
---

## Problem

`finish_json_import` persists the DB then renames JSON to `.imported`.
A crash between those steps leaves `.db` + `.json`. Next load fail-closes
with “manual resolution required” even when the DB is healthy.

## Expected

Crash-safe import (e.g. rename JSON to `.importing` first, or auto-heal
healthy db + leftover json to `.imported`).

## Evidence

- `src/tgcli/clone/statedb.py` (`finish_json_import`)
- `src/tgcli/clone/state.py` (both-files PolicyError)
- Thermos Wave 4 security High#1

## Acceptance

1. Test simulates mid-import crash → subsequent load succeeds without
   manual intervention (or documents a single clear repair command).
2. Full gate green.
