# ADR-0014: Lean faithful channel mirror

Status: accepted (2026-07-13).

Supersedes ADR-0013 as the production implementation design. ADR-0013 and the
expanded laboratory remain useful research records, especially the R0 evidence
that Telethon can read bytes from protected channels, but their full reliability
protocol is not a prerequisite for shipping the personal mirror.

## Context

The product goal is an automatic private copy that looks like the source
Telegram channel. The ADR-0013 plan optimized for forensic proof and
production-grade replication: a five-state outbox, crash injection at every
transition, a large capability matrix, and authoritative deletion audits. That
work is defensible for a multi-user service, but it delays the fidelity features
that matter for this owner-operated tool.

The mirror still needs one hard reliability property: restarting a command must
not create duplicate destination messages. Telegram accepts caller-supplied
`random_id` values for the native copy/send methods, so persisting that value
before dispatch provides the cheapest useful recovery primitive.

## Decision

### Product scope

The first product path mirrors one broadcast channel into one private broadcast
channel owned by the configured account. The vertical slices are delivered in
this order:

1. destination initialization and idempotent text backfill;
2. photos, videos, documents, albums, and replies;
3. a linked plain discussion group and channel comments;
4. foreground watch with catch-up after restart;
5. protected-channel download/reupload.

Forum topics, general/basic groups, member and role replication, reactions,
views, original sender identity, original server timestamps, paid media, and
forensic deletion proof are deferred. A deferred item is not silently claimed
as supported.

### CLI

```text
tg mirror init SOURCE [--commit]
tg mirror sync SOURCE
tg mirror watch SOURCE
tg mirror status SOURCE
```

`init` without `--commit` is a non-mutating preview. `init --commit` creates the
private destination and becomes the one explicit authorization for later
`sync` and `watch` writes for that source/destination pair. All mutation kill
switches and the shared fail-closed audit remain mandatory. `watch` is a visible
foreground command; tgcli installs no daemon or service.

### Durable state

Each resolved `(account_user_id, source_peer_id)` owns one SQLite database at
`TGCLI_STATE_DIR/mirrors/<mirror_id>.db`. It stores:

- canonical source and destination peer ids plus authorization state;
- one source-to-destination message mapping per peer-scoped source key;
- one stable Telegram `random_id` per outbound logical operation;
- a confirmed high-water message id.

There is no five-state outbox. An operation is either unconfirmed (destination
message id is null) or confirmed. The operation and its random id are committed
before network dispatch. Recovery resends the same request with the same random
id; mapping and cursor advance together after Telegram confirms it.

Destination creation has no caller-supplied random id. The database therefore
records a `planned` creation and a unique temporary title marker derived from
the mirror id before dispatch. After an ambiguous result, retry first searches
for an exact private creator-owned channel matching that marker. Zero matches
permits creation; one resumes it; multiple matches stop for operator review.
After authorization, an idempotent profile step changes the visible title to
the source title, so the finished copy remains organic. Automatic orphan
deletion is forbidden.

### Transport and fidelity

Unprotected messages use native server-side copy with `drop_author=True`.
Protected messages use Telethon download/reupload, based on the retained R0
evidence from ADR-0013. Unsupported content becomes an explicit placeholder or
status item; it is never silently dropped.

Linked comments are copied only after the destination broadcast channel has a
private linked discussion megagroup. Reply relationships are resolved through
the persisted message mapping. Forum topics and channel-linked forum semantics
require a later ADR or amendment after the plain-comments slice is green.

## Consequences

- The implementation optimizes for fidelity and useful automation, not formal
  forensic proof.
- Persisted random ids and a cursor protect the common restart path with much
  less code than ADR-0013's state machine.
- Deletion detection can be added later without blocking initial copy, media,
  comments, and watch support.
- The expanded laboratory PR is not merged into the product branch. Individual
  pure helpers or regression tests may be adapted when they directly serve a
  product slice.
