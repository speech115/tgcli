# ADR-0028: Agent correspondence increment — v1.1 scope

Date: 2026-07-18
Status: accepted

## Context

An owner-commissioned external product review of v1.0 concluded that the
project is engineering-complete but product-lopsided: clone is an
industrial subsystem (~2,246 LOC across `src/tgcli/clone/` +
`commands/clone.py`) while everyday messaging is minimal — `send` is 45
LOC of text-only, the message JSON exposes 6 fields, `read` paginates
only by `--limit`, search is per-chat only, and there is no health
check. The owner walked the review through a structured grilling session
on 2026-07-18 and fixed the scope below. Under maintenance mode
(ADR-0026) this ADR plus the scoped plan
[2026-07-18-agent-correspondence.md](../superpowers/plans/2026-07-18-agent-correspondence.md)
constitute the required explicit feature request.

The driving job: **an agent reads, triages, and conducts everyday
Telegram correspondence** — not archival fidelity (clone owns that) and
not business logic (lives above tgcli).

## Decision

Three slices, each independently shippable, all contract-additive:

1. **Read surface.** The message JSON grows additively: `media` stays a
   string (compatibility); a `media_info` object (name, mime, size,
   duration, width, height) appears beside it, plus `permalink`,
   `edited_at`, `outgoing`, `from.username`, `forwarded_from`,
   `reactions`, `topic_id`, `grouped_id`, `is_service`. Pagination via
   explicit flags — `read --before-id/--after-id/--since/--until`,
   `read --topic`, `message --context N`, `search --from/--since` — and
   a `page {oldest_id, newest_id}` hint in responses. No opaque
   cursors: the agent carries the last-seen id itself; tgcli stays
   stateless.
2. **Mutations.** All under the existing preview→commit model. `send`
   gains `--reply-to`, `--file`, `--caption`, `--topic`, `--silent`.
   New commands: `edit`, `delete`, `forward`, `mark-read` (`mark-read`
   is gated by the readonly/no-send switches but needs no preview — it
   is content-free and idempotent). Send/forward commits go through raw
   TL requests with a `random_id` stored in the preview at prepare
   time; the preview lifecycle gains a `.pending` state
   (`.json → .pending → .used`) so re-committing the same preview after
   a network failure is safe — Telegram deduplicates by `random_id`.
   Every commit appends a result audit record (`<command>-result`,
   message id) so "sent or not?" is answerable from `audit.jsonl`.
3. **Discovery and ops.** `dialogs --unread-only --kind` plus a
   `mentions` field; `search --all` (global search); `info --full`
   (role, best-effort `can` map, slowmode, participants, about);
   `tg doctor` — read-only environment/session health report, always
   exit 0 when the check itself ran, failures live in the payload.

**Rejected** (do not build; revisit only with a new ADR):

- `tg spec`/`tg capabilities` — a second source of truth that drifts;
  `SKILL.md` + `--help` remain canonical for agents.
- `tg can`/`tg permissions` matrix — sugar over `info --full` JSON.
- A `tg inbox` command — composable from `dialogs --unread-only` +
  `read --after-id`.
- Keyed idempotency (`--idempotency-key`, `tg mutation status`, durable
  mutation journal) — `random_id` dedup + audit records suffice at
  single-operator scale.
- Opaque pagination cursors — state tgcli would have to version.

**Deferred with triggers** (tracked in [ISSUES.md](../ISSUES.md)):
MSG-001 (albums, scheduled sends, reactions, pin, protect-content,
entities/formatting), FEED-001 (`tg changes` daemonless change feed).
ACCOUNTS-001 (`tg accounts login`) keeps its existing trigger.

## Consequences

- All JSON changes are additive; CONTRACT.md is updated in the same
  commit as each surface change (ADR-0007 discipline). No major bump.
- Clone is not touched. The `random_id` confirmation logic is
  deliberately duplicated into `src/tgcli/confirm.py` instead of
  refactoring the frozen, live-accepted clone subsystem; consolidation
  is possible later if a third consumer appears.
- `safety.consume_preview` remains for clone init; message mutations
  move to the two-phase `begin_commit`/`finish_commit`. The 5-minute
  TTL still bounds the retry window.
- The plan executes slice by slice; each slice ends green
  (`pytest -q`, `ruff check`, `ruff format --check`, `pyright`) and is
  independently mergeable.
