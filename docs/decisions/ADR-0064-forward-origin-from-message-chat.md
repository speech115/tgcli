# ADR-0064: Resolve forward-origin labels from the message's accompanying chat

Date: 2026-07-27
Status: accepted

## Context

Issue #80 (MIAMIVICE PENTHOUSE source `#50`): Telegram clients show the
native forward chrome «Комьюнити Арсена Маркаряна», but clone reupload
prefixed only the bare word `Переслано`. Live dump: `fwd_from.from_id` is
a `PeerChannel`, `from_name` is null, and `get_entity` raises
`ChannelPrivateError` — yet the same `channels.GetMessages` response
already includes that Channel in `chats` (title + `access_hash`, often
`left=True`). Telethon surfaces it as `message.forward.get_chat()`.
ADR-0050 already requires a truthful label from `from_id`; the bug was
resolving `from_id` only via a later GetChannels-shaped lookup.

## Decision

When `forwarded_author_of` cannot resolve `fwd_from.from_id` through
`get_entity`, it next tries `message.forward.get_chat()` /
`get_sender()` and uses the entity only when its id matches the peer.
A hit seeds the author cache. The `from_name` / `post_author` / bare
`Переслано` ladder is unchanged. No fabricated discussion origin.

## Rejected alternatives

- Treating bare `Переслано` as sufficient whenever GetChannels refuses
  (false: Telegram already named the peer on the fetch).
- Calling GetChannels with a hand-built `InputPeerChannel` from a stale
  access_hash scraped elsewhere (extra RPC; the message already carries
  the entity).
- Inventing a label from `channel_post` alone without a title.

## Contract impact

CONTRACT § clone forward-prefix wording clarifies that resolving
`from_id` includes an entity Telegram already shipped with the message
(accompanying chats / Telethon `message.forward`), not only a successful
standalone `get_entity`.
