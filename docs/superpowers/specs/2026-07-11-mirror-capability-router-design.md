# Mirror Capability Router Design

**Status:** SUPERSEDED by ADR-0013 (amended 2026-07-11) after R0 evidence.
Retained for history. The four-tier router (native/Telethon/gotd/unsupported)
is reduced to two live transports; gotd is dropped.

## Goal

Build Telegram mirroring around measured capabilities instead of one assumed
transport. For every chat and message type, tgcli selects the simplest proven
path: native Telegram copy, Telethon reconstruction, a narrow gotd protected
fetch helper, or an explicit unsupported result.

Protected content is required. GUI automation, OCR, screen recording, client
injection, and Telegram Desktop cache scraping are out of scope. A separate
one-time gotd authorization for the same Telegram account is acceptable.

## Product Boundary

The design covers:

- public and private broadcast channels;
- protected broadcast channels where the account is owner/admin or an ordinary
  subscriber;
- linked comments and forum/reply structure when the source exposes them;
- chronological backfill, realtime updates, edits, deletes, and recovery;
- native reconstruction or snapshots for structured content.

Stories are explicitly excluded. Paid media is inspected only when it has
already been purchased and revealed to the account. Probe and mirror never make
purchases, launch giveaways, or initiate payment flows.

General group chats and direct messages use the same capability model later but
are not required for the first implementation plan. Their multi-author rendering
policy remains a separate scope decision.

## Approaches Considered

### Always download and reupload

This gives one conceptual path, but wastes bandwidth when Telegram can copy
server-side, requires temporary files for every media item, and forces tgcli to
reconstruct albums, formatting, polls, and document attributes unnecessarily.

### Gotd for every protected chat

This creates a clean chat-level split, but makes gotd a second full Telegram
client with duplicate update handling, entity resolution, sessions, and error
semantics. It recreates the multi-backend complexity tgcli is meant to avoid.

### Capability router (selected)

Telethon remains the core client. Native Telegram copy is preferred whenever
allowed. Telethon performs protected reconstruction when it can obtain complete
data. Gotd is a narrow read/download helper only for capability rows Telethon
cannot satisfy. Unsupported types are blocked explicitly and never silently
dropped.

## Routing Model

The fixed transport order is:

```text
native copy -> Telethon reconstruction -> gotd fetch -> unsupported
```

The router never tries backends in an unbounded loop and never silently changes
strategy after a runtime failure. It uses a versioned capability profile created
by `tg mirror probe`.

Example profile:

```json
{
  "profile_version": 1,
  "source_peer_id": -1000000000000,
  "protected": true,
  "runtime": {
    "telegram_layer": 224,
    "telethon": "1.44.0",
    "gotd_helper": "0.1.0"
  },
  "capabilities": {
    "text": {"strategy": "telethon", "result": "pass"},
    "photo": {"strategy": "telethon", "result": "pass"},
    "video": {"strategy": "gotd", "result": "pass"},
    "poll": {"strategy": "native_rebuild_snapshot", "result": "pass"},
    "story": {"strategy": "unsupported", "result": "unsupported"}
  }
}
```

Runtime uses only `pass` rows. `not_found`, `blocked`, `unsupported`, and
`inconclusive` never authorize automatic mirroring.

## Probe Design

### Stage A: read-only discovery

`tg mirror probe <chat>` inspects existing source history without publishing,
forwarding, deleting, voting, purchasing, or changing read-visible content.

It:

1. resolves canonical peer and account identity;
2. records public/private/protected flags;
3. classifies available message/media types;
4. tests source decoding and metadata through Telethon;
5. tests complete byte access through Telethon for representative media;
6. invokes gotd only for rows Telethon cannot satisfy;
7. records sizes and SHA-256 evidence;
8. emits a capability report with private content redacted.

Two real protected sources are required for acceptance: one controlled by the
operator and one where the account is an ordinary subscriber. Their names and
message contents remain runtime configuration and are not committed to the
repository.

### Stage B: permanent protected laboratory

A permanent private protected channel owned by the operator supplies controlled
fixtures missing from real history. The lab remains available for regression
checks after Telegram layer, Telethon, or gotd updates.

Fixture manifests contain expected structure, byte size, and SHA-256 but no
account credentials. Fixtures cover:

#### P0: required core

- plain and formatted text, links, entities, and webpage preview;
- photo, generic document, video, GIF/animation, audio/music, voice note, and
  video note;
- static, animated, and video stickers;
- captions, media spoilers, and albums;
- replies, linked comments, and forum-topic replies;
- edit, delete, pin, and protected/noforwards behavior.

#### P1: interactive and structured

- poll and quiz;
- contact, geo, live geo, and venue;
- dice/animated emoji;
- giveaway and giveaway results;
- todo list;
- inline markup when visible, channel signatures, and forwarded metadata.

#### P2: transactional and edge

- already purchased/revealed paid media;
- invoice and game objects;
- TTL/expired content;
- unsupported or newer-layer media;
- suggested/paid suggested posts after publication;
- representative service-event behavior classes.

The pinned Telethon layer exposes 18 `MessageMedia` and 58 `MessageAction`
constructors. The current Telegram schema additionally includes
`MessageMediaVideoStream`. Unknown/new-layer media must produce controlled
`unsupported` or `inconclusive`, never disappearance.

### Probe result states

- `pass`: evidence proves this route works;
- `fail`: reproducibly fails for this fixture;
- `not_found`: no representative source item exists;
- `blocked`: Telegram explicitly denied the operation;
- `unsupported`: deliberately outside supported behavior;
- `inconclusive`: evidence is ambiguous and cannot authorize routing.

Every result records runtime versions, probe timestamp, source capability class,
byte counts/hashes when relevant, and a controlled error category.

## Content Fidelity Policy

### Exact-byte media

Photo/document/video/audio/voice/sticker source bytes must match expected
SHA-256 before reupload. Telegram may transform a reuploaded photo or video in
the destination, so source-fetch integrity and destination representation are
reported separately.

### Native structured reconstruction

- Poll/quiz: create a new native poll with the same question, options, mode,
  correct quiz answers, and solution when available; append a historical
  snapshot of source vote totals. Voters and historical votes cannot migrate.
- Todo: create a native todo with the same title/items/current completion state;
  append a snapshot when source action history is available. Historical actors
  and timestamps cannot migrate.
- Contact and static geo/venue: recreate natively.
- Live geo: recreate only as a static snapshot unless a later spec defines new
  live-state semantics.
- Webpage: regenerate a native preview from the URL and record when the current
  preview differs from the source snapshot.
- Dice: preserve the observed result as a snapshot; sending a new dice would
  generate a different random value.

### Snapshot-only

Giveaway, giveaway results, revealed paid media metadata, invoice, game, and
service events are represented as non-interactive snapshots unless native
server-side copy succeeds. Mirror never launches a replacement giveaway,
creates a new transaction, charges Stars, or impersonates a bot/provider.

Stories are unsupported by product decision.

## Components

### `commands/mirror.py`

CLI facade. Parses mirror commands, acquires clients through `session.py`, emits
contract-shaped results, and never implements transport logic.

### Capability probe

Classifies chat/content, runs read-only backend checks, redacts private data, and
produces immutable versioned profiles. Probe may invoke gotd but never publishes.

### Capability router

Maps `(profile, message_type)` to one frozen strategy. Unknown or stale rows
block safely and request `tg mirror probe --missing`.

### Telethon core

Owns chat resolution, destination creation, native copy, reconstruction,
publishing, event handling, and normal Telegram session lifecycle.

### Gotd protected-fetch helper

A separate local executable with one narrow protocol:

```text
request: account, canonical peer, message id, expected media selector, output
result: status, path, byte count, SHA-256, media metadata, controlled error
```

It performs no destination writes, no update watching, no mirror orchestration,
and no daemonization. It has a separately authorized local session for the same
Telegram user under the canonical tgcli state directory.

### Ledger and operation pipeline

Stores peer-scoped source keys, destination mappings, capability profile version,
transport choice, durable operation state, retries, and changelog facts.
Backfill, watcher, and sync all feed the same operation pipeline.

## Execution Flow

```text
source item
  -> classify exact type
  -> load capability profile row
  -> create durable operation
  -> native copy OR fetch through Telethon/gotd
  -> verify source bytes/structure
  -> publish/reconstruct through Telethon
  -> confirm destination ids
  -> commit source-to-destination mapping
```

Native copy uses Telegram `messages.forwardMessages(drop_author=True)` with
persisted random ids when allowed. Protected native copy is expected to return
`CHAT_FORWARDS_RESTRICTED`; that is a normal `blocked` capability, not a crash.

Temporary files exist only for download/reupload strategies. They live under the
mirror operation state directory, use operation ids rather than untrusted source
filenames, remain for resumable recovery after interruption, and are removed
after confirmed publication.

## Profile Lifecycle

A profile is bound to:

- canonical source peer and Telegram user;
- Telegram layer;
- Telethon version;
- gotd-helper version;
- fixture manifest version;
- probe timestamp.

Version drift marks the profile `stale`. Existing proven routes may continue
until they fail, but new/unknown types do not inherit a strategy. A reproducible
route failure marks only that capability `degraded`, blocks affected operations,
and requests a targeted reprobe. Runtime never silently falls through to an
unproven backend.

## Safety and Privacy

- Stage-A probe is read-only and makes no Telegram mutations.
- Lab fixture creation is a separate explicitly confirmed operation.
- Gotd authorization is explicit and stored locally; session files are never
  committed, logged, or copied into artifacts.
- Probe reports omit message text, private chat names, usernames, filenames, and
  raw Telegram objects by default.
- Paid media is never purchased automatically.
- All destination mutations retain tgcli kill switches and fail-closed audit.
- No GUI/OCR/cache/injection fallback exists.
- No backend may create its own daemon or hidden service.

## Error Handling

Fallback order is fixed. Temporary network errors retry the selected backend
with bounded policy. A reproducible capability failure blocks the operation and
degrades the profile row; it does not move silently to another backend.

Unknown types become `capability_missing` and remain pending until a targeted
probe resolves them. Missing source access, revoked Telethon/gotd authorization,
hash mismatch, partial download, ambiguous send, and unsupported reconstruction
each have distinct controlled status/error categories.

No placeholder is published in place of missing media unless the frozen content
policy for that type explicitly selects snapshot-only representation.

## Verification Strategy

### P0: probe evidence only

Run read-only discovery against the operator-owned protected source and the
ordinary-subscriber protected source, then fill coverage gaps from the permanent
lab. Also run a native-copy control against an unprotected fixture source.

No bulk mirror or destination creation is accepted from this phase. Output is a
capability matrix with evidence and explicit unknowns.

### P1: one end-to-end slice

Implement text and one small photo through:

```text
probe -> route -> fetch/copy -> verify -> publish -> ledger
```

Inject interruption before fetch, during fetch, after fetch, after publish but
before mapping, and during restart. Acceptance requires one destination copy and
automatic recovery without leaked temp state.

### P2: media families

Add independently gated slices for documents, video/GIF, audio/voice/video note,
stickers, albums, and replies/comments. Each enters production routing only after
fixture evidence for its chosen backend and crash-recovery tests.

### P3: structured content

Add poll/quiz and todo native-plus-snapshot, then contact/geo/venue and
snapshot-only transactional/service classes.

### P4: realtime and recovery

Add foreground watch and Telethon catch-up only after backfill is stable. Test
network loss/recovery, process crash, edits/deletes during downtime, stale
profiles, and revoked gotd authorization.

## Stop Criteria

Stop and require an explicit new decision when:

- neither Telethon nor gotd returns complete bytes;
- repeated reads produce inconsistent SHA-256;
- gotd would require GUI, client injection, or a daemon;
- evidence indicates unacceptable account-ban or authorization risk;
- success requires a purchase or new financial transaction;
- a route works only with owner rights but fails for an ordinary subscriber;
- ambiguous publication cannot be reconciled without duplicates.

## Acceptance Standard

A content type is supported only when it has:

1. a reproducible fixture;
2. a selected backend/strategy;
3. integrity or structure evidence;
4. explicit fallback and unsupported behavior;
5. interruption and retry coverage;
6. a privacy-safe diagnostic result.

The project does not claim "protected mirror support" as one boolean. It reports
support per chat, content type, account role, runtime version, and tested route.

## Primary References

- Telegram MessageMedia: https://core.telegram.org/type/MessageMedia
- Telegram content protection: https://core.telegram.org/api/content-protection
- Telegram forwarding/copy: https://core.telegram.org/method/messages.forwardMessages
- Telegram updates: https://core.telegram.org/api/updates
- Telegram polls: https://core.telegram.org/api/poll
- Telegram todo: https://core.telegram.org/api/todo
- Telegram paid media: https://core.telegram.org/api/paid-media
- Telethon client API: https://docs.telethon.dev/en/stable/modules/client.html
- Telethon sessions: https://docs.telethon.dev/en/stable/modules/sessions.html
