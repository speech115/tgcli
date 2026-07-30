# ADR-0066: Expose voice-message playback state in message JSON

Date: 2026-07-30
Status: accepted

## Context

Issue #97 needs agents to distinguish a voice message that Telegram still marks
as unplayed from one that has been listened to. Telethon exposes this as the
`Message.media_unread` flag, but the current universal message projection does
not expose it. The projection is shared by `read`, `search`, `message`,
`latest`, `thread`, and `export`, so the addition must be stable and additive.

## Decision

Add `voice_played` to the universal message JSON shape. For a voice message it
is the boolean inverse of `media_unread`: `false` when `media_unread` is true
and `true` when it is false. For non-voice messages, or when Telegram does not
provide the flag, it is `null`.

The field is derived locally from the fetched Telethon message. It does not
make another RPC and does not mark a message as read or otherwise mutate
Telegram state.

## Rejected alternatives

- Expose raw `media_unread` — leaks a Telegram implementation detail and makes
  the agent-facing meaning harder to consume.
- Emit `false` for every non-voice message — falsely claims that ordinary
  messages have playback state.
- Add a voice-only command — would fragment the existing universal message
  shape used by all read paths.

## Contract impact

`voice_played` is an additive key in every universal message object. Existing
keys and exit codes remain unchanged. `docs/CONTRACT.md`, `SKILL.md`, and the
read tests define the `true`/`false`/`null` semantics.
