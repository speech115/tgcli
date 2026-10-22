# ADR-0122: Sends are paced 3 s apart, in one shared slot

Date: 2026-10-22
Status: accepted
Amends: ADR-0072 decision 3 — sends are no longer unpaced, and for sends the
pace key is the class rather than the request type (the cooldown key stays
the type)

## Context

ADR-0072 decision 3 gave mutations no pre-emptive interval: a mutation was
gated on its cooldown only. That held while sends came one at a time from
`tg send`. `clone sync` sends in a loop.

On 2026-10-22 a catch-up `clone sync` of the `[икона]` clone copied 645
messages. While posts carried media, the 3 s media pace held it to 18–23
sends a minute and nothing flooded. When the run reached text-only messages
nothing slowed it: the audit log shows 94 sends in the first 32 s of
15:57 UTC, about 3 a second, and then a 280 s `FLOOD_WAIT` on
`messages.SendMessageRequest`. The run slept it out under `--max-runtime`,
resumed at full speed, and drew a second wait of 292 s 42 messages later.

Bursting bought nothing. The first burst and its wait came to
(32 + 280) s / 94 ≈ 3.3 s per message; the second, with a 292 s wait after
42 messages, to about 7 s per message. A steady 3 s pace is faster than
either, and repeated floods on one account tend to come back longer.

## Decision

1. **A new paced class, `send`, at 3 s start-to-start.** It holds
   `messages.SendMessageRequest`, `SendMediaRequest`,
   `SendMultiMediaRequest` and `ForwardMessagesRequest`. 3 s is 20 send
   requests a minute: about the rate the media-bound part of the same run
   held without a flood, and the per-group rate Telegram's bot FAQ names.
   The charge is per request, so an album or a multi-id forward is one
   charge. Every other write (e.g. edit, delete, read marks, peer creation,
   and every unlisted type) stays unpaced.
2. **The class shares one pacing slot.** A clone interleaves text sends,
   media sends and forwards into the same chat, and the limit counts what
   lands in the chat, not the method that put it there. Per-type slots
   would let each type send at full pace. The five free requests are
   counted across the class too, so `tg send` and other single commands
   are not slowed.
3. **Cooldowns stay per request type.** ADR-0072 decision 1 is unchanged: a
   flood arms only the type that drew it. The shared slot is a pacing key
   (`registry.pace_key`), not a cooldown key.

## Consequences

- A long clone catch-up should no longer flood: 600 text messages take
  about 30 minutes, instead of bursts separated by 5-minute waits.
- `tg run` scripts that send in a loop are paced the same way.
- The slot is per process (one invocation) and covers all chats: a loop
  that sends one message to each of many chats is also held to 20 sends a
  minute. That is the conservative side; a per-chat slot would need the
  peer in the pace key. Two concurrent processes on one account each get
  their own slot, as for every other paced class.
- The 3 s figure comes from one observed run. If floods on sends still
  occur at this pace, raise the interval; the class and the shared slot
  stay.
