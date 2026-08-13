---
id: T24
priority: P2
labels: [bug, ready-for-agent]
slice: Archive
title: "Archive SQLite: set busy_timeout (document concurrency)"
---

## Problem

Archive `connect` enables WAL but sets no `busy_timeout`. Jobs/governor
stores do. Concurrent sync + search/transcribe/second sync can hit
immediate SQLITE_BUSY.

## Expected

Set busy_timeout consistent with sibling stores; document supported
concurrency (single writer assumption if that remains).

## Evidence

- `src/tgcli/archive/store.py` (`connect`)
- Thermos Wave 5 security Important#5

## Acceptance

1. busy_timeout set; test or architecture note.
2. Full gate green.
