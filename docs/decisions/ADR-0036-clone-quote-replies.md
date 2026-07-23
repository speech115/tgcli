# ADR-0036: Clone resolves quote replies by target reachability, and degrades instead of wedging

Date: 2026-07-23
Status: accepted

Builds on: ADR-0019 (truthful fallbacks), ADR-0023 (channel comments),
ADR-0026 (maintenance mode).

## Context

A live clone of a broadcast channel with comments enabled finished its post
leg — 768 of 768 source posts mapped, the 21 gaps in the source id range being
messages deleted at the source — and then stopped inside the discussion leg at
source comment 2374 with `clone reply shape is not supported`. The cursor never
advanced, so 20 further comments, including that day's entire live
conversation, were held behind one message. Every later run re-hit the same
message and stopped again. Only a tgcli release could clear it.

The message is an ordinary comment. What `clone/replies.py` rejected was its
reply header:

```
reply_to_msg_id = 1244
reply_to_peer_id = PeerChannel(2275285084)   # a third channel
reply_from        = MessageFwdHeader(...)    # server-rendered provenance
reply_media       = MessageMediaPhoto(...)   # server-rendered preview
quote_text        = "что это де-факто не наставничество…"
reply_to_top_id   = 2373                     # an anchor in this group
```

This is Telegram's cross-chat quote reply: the author quoted a fragment of a
message living in another chat. Three findings reshaped the decision.

**`reply_from` and `reply_media` are outputs, not inputs.** They are
server-derived decorations of a header we never have to reproduce.
`InputReplyToMessage` accepts only `reply_to_peer_id`, `reply_to_msg_id`,
`top_msg_id` and the `quote_*` triple. The reject list guarded fields that were
never ours to send.

**The blocking cases were not one case.** Comment 2378, the next blocker,
quotes post 789 of the cloned channel itself — already mapped to destination
773. Its quote is reproducible with no loss at all. Only 2374's target sits
outside the clone.

**Reachability, not peer identity, is the real boundary.** A live canary sent
`SendMessageRequest` into the clone's own discussion group with
`reply_to_peer_id` pointing at channel 2275285084. Telegram answered
`ChannelPrivateError`: the account is not in that channel, which the server
returns as `ChannelForbidden`. The access hash was available; the permission
was not. A quote whose target the account *can* read has no such obstacle.
Separately, the photo embedded in that quote downloads fine (189 KB, all
sizes) despite the channel being forbidden — the file reference outlives the
permission.

A fourth finding is a defect in the read surface rather than the clone:
`tg message 4454061248 2374` reports a bare `"reply_to": 1244`, dropping the
peer. Chat 4454061248 has its own unrelated message 1244, so the output invites
resolving the reply against the wrong message. Tracked separately.

The owner's two requests — clone the missing comment, and stop when something
cannot be cloned — read as opposites only until one notices that the existing
stop does not protect fidelity. It defers divergence while enlarging it: the
mirror falls further behind for every hour it holds out for a perfect copy of
one message.

## Decision

1. **Quote replies are classified by target reachability**, not by the
   presence of `reply_from` / `reply_media` / a foreign `reply_to_peer_id`:
   - **mapped** — the target is inside the clone. Rewrite `reply_to` onto the
     mapped destination message and keep the native quote (text, entities,
     offset). No loss. A target in the other leg resolves through the leg that
     owns it, reaching a post's destination anchor by the path
     `clone/comments.py` already walks for thread roots.
   - **reachable** — the target is outside the clone but readable by the
     account. Point the native quote at the original message. No loss.
   - **unreachable** — the target is outside the clone and not readable.
     No native form exists; take the rendered fallback below.

   Reachability is probed once per peer and cached for the run. A `reachable`
   send that Telegram rejects anyway degrades to `unreachable` rather than
   failing the batch, so the untested branch cannot wedge a sync.

2. **The `unreachable` fallback renders the quote as text**, prefixed by the
   quoted peer's title on its own line and followed by the quote as a
   blockquote, above the author's unmodified text — the shape Telegram itself
   draws for a live cross-chat quote. The message keeps its thread placement.
   `reply_media` is **not** carried: attaching it would turn a text message
   into a media message, a coarser distortion than a lost preview, and one
   Telegram cannot later undo by editing.

3. **Unsupported no longer means wedged.** A message whose form is understood
   but not transferable copies with its loss recorded, and sync continues.
   This extends ADR-0019's fallback principle from poll and Story media to
   reply structure, and covers todo-item, poll-option, ephemeral and scheduled
   reply targets, plus forum reply headers on a non-forum destination: those
   copy as ordinary messages with the reply relation dropped and reported —
   the existing `reply_flattened` outcome — since they carry no quote text to
   preserve.

4. **Fail-closed narrows to the not-understood.** An invalid reply parent, an
   album whose items disagree, and any unrecognized header type still exit 2
   before audit or mutation. The rule is that clone stops when it cannot trust
   its reading of the data, not when it cannot carry what it read. Blind
   mapping of a cross-peer `reply_to_msg_id` is exactly the failure this
   guards: it would have produced a reply pointing at an unrelated message,
   indistinguishable from a correct one.

5. **A degraded run reports and exits nonzero.** Sync completes its work,
   advances cursors, writes the full result document, and raises
   `PartialFailure` (ADR-0032) carrying the `PolicyError` exit code when the
   run planted at least one fallback. JSON gains `quote_flattened` — a list of
   `{"id": N, "peer": M, "reason": "..."}` — beside `skipped_unsupported`, and
   plain output gains a `quote_flattened_count` column. Later runs that plant
   nothing exit 0.

6. **One rule for both legs.** Posts and comments are classified identically.
   Nothing in the classification depends on which leg a message belongs to
   except which map its target resolves against.

## Consequences

The wedged clone resumes with no state repair: because the cursor never
advanced, nothing downstream was corrupted, and an ordinary `tg clone sync`
catches up.

Quoting a post of the channel from its comment thread — the most common way a
reader quotes anything — becomes lossless, where today it halts the mirror.
Fallbacks are confined to quotes of chats the account cannot read.

A cloned `unreachable` quote carries text the author did not write. The peer
title above the blockquote is what distinguishes it from authored content, and
it is weaker evidence than a live quote's clickability: in the destination the
two differ only in that one navigates and the other does not.

Automation that treats a nonzero exit as failure will see a fallback-planting
run as failed while it in fact completed. This is intended — the exit code is
the notification — but it makes `clone sync` unsuitable for an unattended
chain without inspecting the result document.

Quote text and entities remain editable in the destination afterwards; the
reply relation does not, because Telegram fixes `reply_to` at send time and
`messages.editMessage` cannot change it. Every decision above therefore
prefers spending fidelity on decoration rather than on structure.

## Alternatives rejected

**Keep failing closed and add richer diagnostics.** Names the blocking message
and field, which the current error does not, but leaves the mirror stopped and
the backlog growing until a release ships. Diagnostics without progress solve
the smaller half of the problem.

**One fallback for every quote reply, without classification.** A single
evening's work, but it renders the common mapped case — a reader quoting a post
— as pasted text when it could be reproduced exactly, and makes fallbacks the
norm rather than the edge.

**Carry `reply_media` into the fallback.** The bytes are obtainable. It would
change a text message into a media message, a difference of kind rather than
degree, and Telegram offers no edit that converts one back into the other.

**Silently skip what cannot be copied.** Matches how unsupported media behaves
today, and was rejected for the reason ADR-0019 already recorded: skipping
preserves progress at the cost of position, and hides the loss precisely from
the person who asked to be told about it.
