---
id: T17
priority: P2
labels: [bug, ready-for-agent]
slice: CLI shell
title: "Redact RUNTIME error envelopes (mask phones)"
---

## Problem

Unhandled exceptions become `RUNTIME` with raw `str(exc)` on stdout
JSON and stderr. CONTRACT expects phones masked in JSON/stderr/audit;
this path can leak PII from Telethon messages.

## Expected

Mask phones (and avoid dumping raw library text where possible) in the
RUNTIME envelope path. Verbose traceback stays stderr-only.

## Evidence

- `src/tgcli/cli.py`, `output.py`
- Thermos Wave 8 security Medium#2

## Acceptance

1. Test: exception text containing a phone is masked in JSON/stderr.
2. Full gate green.
