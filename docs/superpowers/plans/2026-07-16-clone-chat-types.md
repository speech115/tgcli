# Clone chat types: supergroups and private dialogs (hybrid reply transport)

> Status: completed and live-accepted 2026-07-16 (ADR-0021). Built on the
> landed ADR-0019 truthful-fallbacks and ADR-0020 profile-copy work.

## Goal
`tg clone` accepts two new source kinds in addition to broadcast channels:

- **megagroup supergroups** (non-forum),
- **private 1:1 dialogs** (User peers).

Destination stays a private owned broadcast channel (unchanged). Exact
source ordering, resume semantics, cooldowns, and the safety
preview→commit flow are all unchanged.

## User-approved product decision (hybrid attribution)
For attributed sources (dialog/megagroup), Telegram cannot combine
"forwarded from" headers with reply threading (`ForwardMessagesRequest`
silently drops `reply_to`). The approved trade-off:

- **Non-reply messages → native forward with `drop_author=False`.**
  Author attribution comes from the "Forwarded from X" header; original
  media, nothing re-uploaded. (If the author restricted forward linking,
  the header shows a non-clickable name — acceptable, do not special-case.)
- **Reply messages → reupload with author-name prefix.** Send via the
  existing reupload transport with `reply_to` resolved from `id_map`,
  prepending `"{author}: "` to the text/caption so attribution survives.
  Unmapped parent → the ADR-0019 fallback (flatten + report), i.e. for
  attributed sources: forward with `drop_author=False`, no prefix.
- **Protected content (noforwards) from attributed sources → reupload
  with prefix always**, otherwise attribution is silently lost.
- **Albums:** whole-album decision — reply album → reupload
  (`SendMultiMediaRequest`) with prefix on the album caption; otherwise
  forward the album natively with `drop_author=False`.

Broadcast-channel sources keep today's behavior exactly
(`drop_author=True`, reupload only for protected/replies). Transport mode
is derived from source kind, never a CLI flag.

## Design
- **Validation** (`_resolve_source`, src/tgcli/commands/clone.py):
  - accept: broadcast channel (current), `Channel` with `megagroup=True`
    and `forum=False`, `User` with `bot=False`.
  - reject with distinct `PolicyError` messages: forum supergroups
    ("forum topics are not supported"), basic legacy `Chat` groups,
    bots, and anything else. Bots are explicitly out of scope per user.
- **State:** add `source_kind` (`"broadcast" | "megagroup" | "dialog"`)
  to the per-clone JSON state, written by `clone init`. Loading a state
  file without the field defaults to `"broadcast"` (existing clones keep
  working; still no migrations). `clone status` shows the kind.
- **Author names:** resolve once per sync from message `from_id` /
  `out` flag (dialog: the two participants; megagroup: cache
  `sender_id → display name` lookups). Display name = first_name +
  last_name for users, title for channel senders, fallback `"id <n>"`.
  Never hit the network per-message when the sender repeats.
- **Prefix mechanics (gotcha):** message `entities` offsets are in
  UTF-16 code units and point at the original text. When prepending
  `"{author}: "`, shift every entity offset by the UTF-16 length of the
  prefix. Put prefix/entity-shift helpers in a small new module
  `src/tgcli/clone/attribution.py` (budget ≤80 lines) so
  `commands/clone.py` stays within its ≤400-line budget.
- **View-once / TTL media:** cannot be forwarded or re-downloaded —
  skip with `skipped_unsupported` reporting (existing mechanism).
- **Service messages** (joins, pins, calls): skip + report, same as
  channel service messages today.
- **Contract/docs:** update `docs/CONTRACT.md` §clone: accepted source
  kinds, hybrid attribution rules, and per-run counters already present
  (forward vs reupload vs skipped) must stay truthful. Note the decision
  in `docs/PLAN.md`; DEVLOG entry as usual.

## Tests
- Validation matrix: megagroup accepted, forum rejected, basic `Chat`
  rejected, bot `User` rejected, plain `User` accepted, broadcast
  unchanged.
- Transport routing for attributed sources: non-reply → forward with
  `drop_author=False`; reply → reupload with prefix + `reply_to`;
  protected → reupload with prefix; broadcast source regression-guarded
  to `drop_author=True`.
- Prefix correctness: UTF-16 entity offset shift (use a text with
  emoji + bold entity), caption prefix on album reupload.
- Unmapped-parent fallback for attributed sources (forward, no prefix,
  reported).
- State: `source_kind` round-trip, default for legacy files.
- View-once media skip.

## Live acceptance (user rule: visual, not test counts)
1. Clone a small owned supergroup → visual order + attribution check.
2. Clone an own 1:1 dialog → verify forwards show both authors' headers,
   replies show prefix + working quote jump.
3. Idempotent rerun on both: 0 copied.

### Result

- Owned megagroup fixture: 27 mapped in strict order, two visible native
  authors, zero-copy rerun. A second organic megagroup added 102 mapped rows,
  30 attributed reply reuploads, and 30/30 reply links.
- Small dialog: 11 native forwards, two visible authors, strict order,
  zero-copy rerun.
- Reply-bearing dialog: 53 mapped in strict order, 49 native forwards, four
  prefixed reuploads, 4/4 author prefixes, 4/4 reply links, zero-copy rerun.
- Live discovery: `MessageReplyStoryHeader` has no message id and now uses the
  reported flatten fallback. Additive JSON counters expose transport and
  flattening rather than leaving either implicit.

## Out of scope
Bots (user-confirmed), forum topics (needs topic-map spec/ADR), basic
legacy groups, group/megagroup destinations, comments/watch, secret
chats (not reachable via API).
