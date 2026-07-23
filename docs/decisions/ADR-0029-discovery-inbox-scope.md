# ADR-0029: Discovery & inbox surface — quick-win scope

Date: 2026-07-21
Status: accepted

## Context

After v1.1 (ADR-0028) shipped the everyday-correspondence surface, the owner
reviewed a wider feature wishlist (captured in [PROPOSALS.md](../PROPOSALS.md))
against the real CLI. Most items were either already shipped, already deferred
with triggers (MSG-001, FEED-001, ACCOUNTS-001), or large verticals that stay
in the backlog. Five small, high-leverage items remained genuinely unaddressed
and were put through a structured grilling session on 2026-07-21.

The driving job: **an agent finds the right peer, surveys what a chat contains,
follows a real conversation, and keeps its own inbox in order** — the discovery
and housekeeping layer beneath correspondence (ADR-0028) and clone (ADR-0017…).

Under maintenance mode (ADR-0026) this ADR plus its scoped plan
[2026-07-21-discovery-inbox.md](../superpowers/plans/2026-07-21-discovery-inbox.md)
constitute the required explicit feature request.

## Decision

Three independently shippable slices. Read commands follow the existing
JSON/`--plain` contract; mutations reuse the ADR-0028 gating.

1. **Identity (read).**
   - `tg resolve <@username | t.me link | numeric id | +phone>` → a single
     peer object `{peer: {id, type, username, display_name, is_contact,
     is_bot}}`. Username/link/id go through `contacts.resolveUsername`
     (already read-allowlisted). Phone goes through **`contacts.resolvePhone`
     only** — added to the read allowlist. Phone resolution never falls back
     to `contacts.importContacts`: it must not add anyone to the account's
     contact list, and it returns an empty/not-found result when the target's
     privacy disallows phone lookup.
   - `tg contacts list` and `tg contacts search <query>`. `search` filters the
     local address book (`contacts.getContacts`) by default; `--global` opts
     into `contacts.search` (public, whole-Telegram) and is clearly labelled
     as such.

2. **Inbox (mutations, no preview).** `tg mark-unread <chat>` (top-level, to
   mirror the shipped top-level `mark-read`) and `tg dialog pin/unpin <chat>`.
   These are content-free, reversible, and idempotent, so — exactly like
   `mark-read` under ADR-0028 — they run directly, gated only by
   `--readonly`/no-send, with **no** preview→commit and no `random_id`. Each
   commit appends a result audit record.

3. **Discovery (read).**
   - `tg media manifest <chat>` — a dry-run inventory (per item: message id,
     type, size, mime, filename), no download. Filters `--type`
     (photo/video/audio/voice/document), `--since`, and `--limit` (default
     100) keep it economical; iterating a whole large channel unbounded is out
     of scope.
   - `tg thread <chat> <id>` → `{root, ancestors[], replies[]}`. **Ancestors**
     always: walk `reply_to` upward, bounded by `--depth` (default 20, hard
     cap 100) so the walk cannot run away in round-trips. **Replies** only
     with `--replies`, and only via `messages.getReplies` where the message
     has a comment/forum thread; where no cheap thread API exists, `replies`
     is `[]` with a `note`. No history scanning for replies in plain groups.

**Rejected** (do not build; revisit only with a new ADR):

- `resolve +phone` via `importContacts` — it mutates the contact list and
  notifies the target; `resolvePhone` is the only acceptable path.
- `thread` reply reconstruction by scanning a plain group's history — an
  unbounded, FLOOD_WAIT-prone operation contrary to the economy principle.
- `contacts search` defaulting to global — surprising for a command named
  "contacts"; global stays behind an explicit `--global`.
- preview→commit for the inbox mutations — inconsistent with the ADR-0028
  rule that content-free idempotent mutations need no preview.

**Deferred, still in [PROPOSALS.md](../PROPOSALS.md)** (not this ADR):
`mutual-chats`, bulk media *download* filters, `export --after-id/--append/
--resume` and `export bundle`, `tg batch`, `dialog archive/mute`, and the
community/moderation, channel-stats, and account-security verticals.

## Consequences

- Read allowlist grows by one method (`contacts.resolvePhone`); this is the
  only safety-surface change and is why an ADR is required. All other read
  commands add no new policy.
- All JSON is additive and CONTRACT.md is updated in the same commit as each
  surface change (ADR-0007). No major version bump.
- The inbox mutations reuse `safety.enforce_mutation_allowed` and the audit
  writer; they do **not** touch the preview subsystem. Clone and the ADR-0028
  message mutations are untouched.
- New command modules (`commands/identity.py`, `commands/dialog.py`,
  `commands/thread.py`) and `commands/media.py`/`commands/mutate.py` additions
  follow existing layering: module owns logic + `to_rows`; `cli.py` owns
  parsing, gating, dispatch. MAP.md gains their rows in the same commits.
- The plan executes slice by slice; each slice ends green (`pytest -q`,
  `ruff check`, `ruff format --check`, `pyright`) and is independently
  mergeable.
