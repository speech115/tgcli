# ADR-0031: Full broadcast subscriber export via prefix search

Date: 2026-07-23
Status: accepted

## Context

Telegram hard-caps `channels.getParticipants` at 200 rows for broadcast
channels: pagination past that offset returns empty, and even `.count` on the
response often reads 200. A single `iter_participants` pass therefore cannot
export a full subscriber list. A 2026-07-22 change introduced saturating
prefix-union search to recover members past that cap, but initially also
triggered that full walk whenever `--limit > 200`, then sliced an unordered
dedupe map — a semantics footgun that review of PR #18 rejected.

## Decision

1. **Unlimited broadcast export** (`--limit` omitted): run
   `_iter_all_channel_members` — sequential saturating
   `ChannelParticipantsSearch` prefix queries over a latin+digit+cyrillic
   alphabet, stop when the reported member total is reached (or the search
   space is exhausted). Some emoji/CJK-only display names may remain
   unreachable.
2. **`--limit ≤ 200`**: keep a single `iter_participants` pass (recent page).
3. **Broadcast + `--limit > 200`**: exit 2 (`BLOCKED`) with an explicit
   message — do **not** full-crawl then truncate. Omit `--limit` for a
   complete export.
4. Megagroups are unchanged (normal pagination works).

## Consequences

- CONTRACT.md documents the aggressive path and the blocked finite-limit
  case in the same commit as the code.
- This ADR is the architectural record for the behavior previously noted
  only in DEVLOG; it does not expand ADR-0029 scope.
- Wall time for a full export is bounded by Telegram flood limits on
  `getParticipants`, not local CPU.
