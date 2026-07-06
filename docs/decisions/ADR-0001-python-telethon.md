# ADR-0001: Python 3.12 + Telethon (not Go, not TDLib-first)

Status: accepted (2026-07-06)

## Context
gogcli is Go and we admire its architecture. A Go rewrite would use gotd or
gogram — both notably less mature than Telethon for user-account MTProto,
media edge cases, and takeout. We already own authorized Telethon sessions
for 4 accounts and years of Telethon-specific know-how in `tools/telegram`.

## Decision
Python 3.12, Telethon (>=1.36), uv-managed project. TDLib stays available
as an optional media backend (ADR-0006), never as the core client.

## Consequences
- We copy gogcli's architecture, not its language.
- Sessions from the old stack import directly (same library, same format).
- Single-binary distribution is off the table — fine, distribution is a
  v1 non-goal (PLAN.md).
