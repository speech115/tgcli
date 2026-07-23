# ADR-0032: Data plumbing & inbox ergonomics

Date: 2026-07-23
Status: accepted

## Context

After ADR-0029 shipped identity, inbox pin/unread, media manifest, and
thread, the remaining high-leverage items in [PROPOSALS.md](../PROPOSALS.md)
were: `mutual-chats`, `dialog archive/mute`, incremental `export messages`,
bulk `media download`, and read-only `tg batch`. The owner approved that
set (not `export bundle`, MSG-001, FEED-001, ACCOUNTS-001, or
moderation/stats/security verticals). A grilling session on 2026-07-23
locked contract edges that agents otherwise trip over (exit codes, mute
forever, append/resume, batch and download caps).

Under maintenance mode (ADR-0026) this ADR plus
[2026-07-23-data-plumbing.md](../superpowers/plans/2026-07-23-data-plumbing.md)
are the explicit feature request.

## Decision

One ADR, five independently shippable slices, one PR with five commits:

1. **`tg mutual-chats <user>`** — read wrapper over `messages.getCommonChats`
   (already allowlisted). Returns `{peer, chats[], count}`. Exit 4 if the
   user is missing; empty `chats` is success.
2. **`dialog archive|unarchive|mute|unmute`** — content-free inbox mutations
   like pin (ADR-0029): no preview→commit; `enforce_mutation_allowed` +
   pre-network audit. Mute requires `--until ISO8601` **or** `--forever`
   (mutually exclusive); silent forever is forbidden.
3. **Incremental `export messages`** — `--after-id`, `--append`, `--resume`.
   `--append` only with `--after-id` or `--resume` (else exit 2). `--resume`
   reads the last JSONL line's message `id` (corrupt/empty → exit 1). Full
   replace without these flags stays atomic as today.
4. **Bulk `media download`** — `--message-ids` and/or manifest-like
   `--type`/`--since`/`--limit` (default 100). Hard cap **100** downloads
   per invocation. No unbounded `--all`. Per-item NotFound → `failed[]` and
   continue; FloodWait/auth stop. Any non-empty `failed[]` → nonzero exit;
   successful files remain on disk.
5. **`tg batch --json`** — read-only JSONL stdin→stdout, one session,
   sequential ops. Allowlist: dialogs/read/search/latest/message/info/count/
   resolve/contacts/mutual-chats/media manifest/thread. Not allowed: doctor,
   mutations, export, clone, media download, api, accounts. Hard cap **100**
   ops. Process exit 0 only if every op succeeds; otherwise first failure's
   code (full JSONL unless `--fail-fast`).

## Consequences

- CONTRACT.md, SKILL.md, MAP.md, and PROPOSALS.md update in the same commits
  as each slice.
- Archive/mute reuse the pin safety seam, not the send/edit preview path.
- Batch does not become a transaction language; mutations stay out of v1.
- Large exports/downloads are agent-driven loops of capped invocations, not
  unbounded single commands.
