# ADR-0021: Clone supports attributed chat sources

Status: accepted (2026-07-16).

Extends ADR-0017 from broadcast channels to non-forum megagroups and private
one-to-one dialogs. Amends ADR-0019 reply fallback reporting and extends
ADR-0020 profile copy to User sources.

## Context

Megagroups and dialogs have visible per-message authors. Telegram native
forwarding can retain that attribution, but `forwardMessages` cannot attach a
new destination reply target. Reupload can rebuild the reply graph, but loses
Telegram's native forward header. The source kind therefore determines one
hybrid transport policy.

The first dialog live gate also exposed `MessageReplyStoryHeader`: a reply to a
Story has no source message id and can never be mapped into the destination.

## Decision

- Accepted sources are broadcast channels, non-forum megagroups, and non-bot
  User dialogs. Forum megagroups, legacy basic groups, bots, and other peer
  shapes fail with distinct policy errors.
- State records `source_kind` as `broadcast`, `megagroup`, or `dialog`. Legacy
  state without the field defaults to `broadcast`; invalid values fail closed.
- Non-reply attributed messages and albums use native forwarding with
  `drop_author=False`. Broadcast sources retain `drop_author=True`.
- A mapped reply or protected attributed message uses reupload and prepends
  `<display name>: ` to text or the leading album caption. Existing entity
  offsets shift by the prefix's UTF-16 code-unit length. Sender resolution is
  cached once per unique peer during a sync run.
- If the direct parent is unmapped, content copies without a reply relation and
  increments `reply_flattened`. Unprotected attributed content falls back to a
  native attributed forward. A mapped direct parent is preserved even when an
  optional nested top root is unavailable.
- `MessageReplyStoryHeader` is a truthful flatten case because it has no
  message id. Other malformed, cross-peer, scheduled, forum, ephemeral, todo,
  poll-option, reply-from, and reply-media shapes remain fail-closed.
- TTL/view-once photo and document media advance the cursor and are reported in
  `skipped_unsupported`; tgcli does not pretend their bytes are recoverable.
- Sync JSON reports `forwarded`, `reuploaded`, `snapshots`, and
  `reply_flattened` alongside the existing counters.
- Attribution mechanics live in `clone/attribution.py` (80-line budget) and
  reply validation in `clone/replies.py`. `commands/clone.py` remains capped at
  400 lines and `clone/state.py` at 150. The expanded validation/transport
  matrix raises ADR-0017's approximate clone-test budget to 1,800 lines.

## Consequences

Attributed sources preserve native author headers wherever Telegram can do so
and preserve clickable reply jumps where reconstruction is required. Reuploaded
messages are visibly attributed by text rather than a Telegram forward header.
Flattening is observable in command output instead of being silent.

The destination remains a private creator-owned broadcast channel. Forum topic
maps, basic-group sources, bots, group destinations, comments/watch, and secret
chats remain outside this slice.

## Live evidence

- Owned megagroup fixture `mirror dm small test: Ioann Himmerfel`: 27 mapped
  messages in strict order, two native forward authors, and a zero-copy rerun.
- Organic megagroup `Насрал в настав Messages`: 102 mapped in strict order;
  72 native forwards, 30 reuploads, 9 distinct native forward authors, and
  30/30 mapped reply links. Zero-copy rerun passed.
- Dialog `@unattuna`: 11/11 native forwards in strict order with two distinct
  native forward authors. Zero-copy rerun passed.
- Dialog `@zabudskiy_dmitry`: 53 mapped messages in strict order; 49 native
  forwards, 4 reply reuploads, two native forward authors, 4/4 reply links,
  and 4/4 visible author prefixes. Its final Story reply flattened explicitly;
  zero-copy rerun passed.
