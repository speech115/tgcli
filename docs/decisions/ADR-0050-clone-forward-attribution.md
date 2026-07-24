# ADR-0050: Clone attribution for reposted (forwarded) source posts

Date: 2026-07-25
Status: accepted

## Context

A channel post that is itself a forward — typically the owner reposting a
subscriber's comment out of the linked discussion group — carries a native
Telegram header naming who wrote it. The clone loses that header entirely and
puts nothing in its place.

Observed live on `[икона]`: source `t.me/c/3802378977/69` has
`fwd_from.from_id = PeerUser(973293498)`; its clone
`t.me/c/4273081187/45` has `fwd_from = None` and no other trace of origin.
The post reads as an original statement by the channel.

Two independent causes:

1. **The native header cannot survive a reupload.** The source is
   `noforwards=true`, so `transport.decide` picks `reuploaded`
   (`clone/transport.py:52-57`); a reupload is a fresh `SendMessage` and no
   send method can fabricate a `fwd_from`. `_drops_author`
   (`commands/clone.py:739`) already preserves original forward headers on
   the *forward* path — the reupload and snapshot paths have no equivalent.
2. **The broadcast leg never attributes.** `needs_author = leg.source_kind
   != "broadcast" and mode != "forwarded"` (`clone/transport.py:60`), so a
   broadcast post gets no author line even as plain text. The rule is right
   for ordinary channel posts (the channel *is* the author) and wrong for a
   post whose content demonstrably came from someone else.

The discussion leg is unaffected: `quotes._place_thread` forces
`as_reuploaded` to place a comment under the destination anchor
(`clone/quotes.py:247`), which sets `needs_author=True`, so comments already
carry their real author as a text prefix. The gap is specific to the posts
leg.

A rejected shortcut motivates the decision below. Once the comments leg has
run, the clone's discussion group holds a copy of the original comment, and
that copy could be forwarded into the clone channel to produce *some* native
header. It must not be: the copy was sent by the cloning account, so
Telegram would render "Переслано от «account owner»" — naming the wrong
person, omitting the discussion-group origin entirely (a user's group
message forwards as the user, never as the group), and contradicting the
copy's own body prefix, which names the real author. A confidently wrong
attribution is worse than a visibly absent one.

## Decision

1. **Attribute reposted posts on the non-forward paths.** When a posts-leg
   batch travels as `reuploaded` or `snapshots` and its leading message has
   `fwd_from`, the body gains an attribution prefix. `needs_author` stops
   being a pure function of `source_kind`: a broadcast post qualifies when,
   and only when, it is itself a forward.

2. **Render exactly what `fwd_from` asserts — never more.** The prefix reads
   `Переслано от <label>` (Telegram's own wording for this shape), where
   `<label>` resolves in this order:
   - `fwd_from.from_id` → resolved entity name, with `@username` or a
     `MessageEntityMentionName` exactly as `attribution._identify` already
     does for the discussion leg;
   - `fwd_from.from_name` → the string verbatim, no mention (the author hid
     their account; we must not re-link it);
   - `fwd_from.post_author` → the channel signature;
   - nothing resolvable → `Переслано` with no name.

   `saved_from_peer` is `null` on this shape, so the clone never claims
   "из обсуждения" — it does not know that, and Telegram does not say it.
   This reuses `attribution.with_prefix`; only the label construction is new
   (`author_of` reads `message.from_id`, which for a channel post is the
   channel, so a `fwd_from`-reading sibling is required).

3. **Optional upgrade: forward the real original when it can be proven.**
   The linked source group is frequently *not* protected even when the
   channel is (verified on `[икона]`: group `noforwards=false`, channel
   `noforwards=true`). Where that holds, the clone may reproduce a genuine
   header by forwarding the original comment out of the source group into
   the destination channel. This is permitted only when **all** hold:
   - the source discussion group is reachable and `noforwards` is false;
   - exactly one group message matches `fwd_from.from_id` **and**
     `fwd_from.date`;
   - that message's text and media match the channel post exactly.

   The content check is load-bearing, not belt-and-braces: a repost is
   routinely edited afterwards (source 69 was edited at 16:18 UTC, having
   been reposted at 13:55), and forwarding the unedited original would
   silently publish different text. Any failed condition falls back to
   decision 2 — the clone never guesses which message it was.

4. **Sequencing.** Decisions 1–2 ship first and stand alone. Decision 3 is a
   separate slice behind its own release; it costs one `messages.Search` per
   reposted post and must not be implemented until the owner accepts that
   per-post RPC against the ADR-0045 flood budget.

## Consequences

- Reposted posts stop reading as original channel statements. The clone
  gains a truthful, plain-text origin line instead of a missing one.
- The prefix is destination content, not CLI output, so it is Russian —
  consistent with the ADR-0048 poll placeholder.
- Attribution is additive to the body; JSON, cursors, and transport counts
  are unchanged. No contract change.
- Decision 2 costs at most one `get_entity` per distinct forwarder, served
  by the existing `author_cache`. Zero extra RPCs for an unresolvable or
  already-cached author.
- The clone still cannot reproduce a native header for a protected source
  under decisions 1–2. That is accepted: the information is preserved, its
  presentation is not.
- Decision 3, if taken, makes the clone's fidelity depend on a match rather
  than on a Telegram-supplied pointer. The three conditions are what keep a
  mismatch from becoming a false attribution, and they are mandatory.
