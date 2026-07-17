# ADR-0025: Preserve the native forward header on re-forwarded broadcast posts

Date: 2026-07-17
Status: accepted
Builds on: ADR-0021 (attributed sources; `drop_author=True` for broadcast).

## Context

ADR-0021 forwards broadcast-channel posts with `drop_author=True` so the clone
reads as its own channel rather than announcing "Forwarded from <source>" on
every post. Live observation on `@sral_v_nastav` (2026-07-17) exposed the side
effect: **32 of 100 sampled posts were themselves forwards** (they carried a
`fwd_from` header — re-forwards from other users, channels, and stories), and
`drop_author=True` erased *that* original header too. In the clone those posts
looked like native content, with no indication they were forwarded and no link
to their true origin.

`drop_author` is about the *immediate* peer being forwarded from — the source
channel. A re-forward's true origin is not the source channel; it is the
original author Telegram records in `fwd_from` (`saved_from`). Telegram
preserves that original origin across a re-forward when `drop_author=False`: it
never substitutes the intermediate channel. This was confirmed live — forwarding
source posts 691 and 679 into the clone with `drop_author=False` produced
headers pointing at "Иван Якунин" and channel `1732547702/4497` respectively,
never at the source channel `4301599563`.

## Decision

- **Per-batch `drop_author` for broadcast sources.** A broadcast post that is
  the channel's own content (no `fwd_from`) still forwards with
  `drop_author=True` — the clone stays native and never leaks the source
  channel. A post that is itself a forward (any message in the batch has
  `fwd_from`) forwards with `drop_author=False`, so Telegram restores its
  original forward header. A forwarded album carries `fwd_from` on every item,
  so the whole batch decides as one. Non-broadcast sources are unchanged (they
  already use `drop_author=False`).
- Decision lives in `_drops_author(leg, messages)` in `commands/clone.py`;
  the transport mode ("forwarded") is unchanged, so this stays a cheap native
  forward with no extra download/reupload.

## Consequences

- Re-forwarded posts in the clone now carry the same forward header they had at
  the source: clickable for channel/open-user origins, a bare name for
  forward-privacy users — matching exactly what the source displayed.
- **Limitation:** only the native "forwarded" transport preserves the header. A
  re-forward that also has a mapped reply, or any post from a `noforwards`
  (protected) source, travels by reupload, which rebuilds fresh content and
  cannot carry a `fwd_from` — its origin is still lost. A protected source
  cannot be forwarded at all, so no header preservation is possible there. A
  text-marker fallback for those paths is deferred until demonstrated need.
- Forwarded stories remain a separate case: they arrive as `MessageMediaStory`
  and are rendered by the ADR-0019 snapshot path ("Stories недоступна / Автор"),
  not the `fwd_from` header path.
