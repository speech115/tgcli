# Clone chat types, round 2: bot dialogs, basic groups, forum topics

Date: 2026-07-16
Status: approved (design), not yet implemented
Builds on: ADR-0021 (attributed sources), plan `2026-07-16-clone-chat-types.md`.

## Problem

Clone accepts broadcast channels, non-forum megagroups, and non-bot 1:1
dialogs. Three source shapes still fail with policy errors: bot dialogs,
legacy basic `Chat` groups, and forum megagroups (ISSUES CLONE-002). The
destination is hard-wired to a private owned broadcast channel — fine for
linear sources, but a forum clone without topics would flatten its structure.

## Decisions (resolved with the user, 2026-07-16)

- **Bot dialogs become a supported source.** This reverses the "bots
  (user-confirmed)" exclusion from the 07-16 plan: the exclusion was about
  Bot API / bot behavior, not about reading a dialog with a bot over MTProto.
  Reading a bot dialog does not touch the project-wide "no Bot API" non-goal.
- **Forum sources clone into a private owned forum megagroup, topics 1:1.**
  The CONTRACT §11 invariant "destination is always a private owned broadcast
  channel" is replaced by: **destination type follows source kind** — forum →
  forum megagroup, everything else → broadcast channel (unchanged).
- **No general group destinations.** No `--dest-type` flag, no cloning into
  existing groups. The forum megagroup is the only non-broadcast destination
  and it is tool-created and tool-controlled like today's channels.
- **Basic groups clone like megagroups**: broadcast-channel destination,
  hybrid attribution transport, `"{author}: "` prefixes on reuploads.

## Goals

- Three new accepted source kinds: `dialog` with `bot=True`, basic `Chat`
  groups (`source_kind: "basic"`), forum megagroups (`source_kind: "forum"`).
- Order fidelity, resume semantics, cooldowns, preview→commit safety flow,
  and the hybrid attribution rules of ADR-0021 all carry over unchanged.
- Forum clones reproduce the topic structure: every source topic maps to a
  destination topic; every message lands in the topic that mirrors its
  source topic.
- Budgets: `commands/clone.py` stays ≤400 (currently 390 — forum logic must
  live in the new module, not here); new `clone/topics.py` ≤100;
  `clone/state.py` raised 150 → 170 for the two new fields (recorded in
  ADR-0022). Exceeding a budget requires cutting before adding.

## Non-goals

- Cloning into pre-existing or shared groups; public destinations.
- Preserving topic closed/hidden flags (v1 creates all topics open).
- Comments/watch, secret chats, Bot API — unchanged non-goals.

## Slice 1 — bot dialogs (small)

- `clone/attribution.py` `source_kind()`: drop the `entity.bot` rejection;
  a bot `User` returns `"dialog"` like any user.
- Everything downstream is already generic: hybrid transport (non-reply →
  native forward `drop_author=False`, reply/protected → prefixed reupload),
  author names via `first_name`, profile copy via `GetFullUserRequest`.
- CONTRACT §11: remove "bots" from the rejection list.

## Slice 2 — basic legacy groups (medium)

- `source_kind()`: `types.Chat` → `"basic"` instead of `PolicyError`, with
  two honest rejections kept:
  - `migrated_to` set → `PolicyError` "group migrated to a supergroup; clone
    the supergroup instead" (naming the target when available);
  - `deactivated` → `PolicyError` (dead husk, nothing meaningful to clone).
- Treated as an attributed source: identical transport and attribution rules
  as `"megagroup"`. Destination: broadcast channel, unchanged.
- History iteration goes through the `Chat` entity (`PeerChat` id space —
  its own per-group message ids, so the single cursor works as-is).
- `clone/profile.py`: add a `GetFullChatRequest` branch for about/photo
  (currently only `GetFullUserRequest`/`GetFullChannelRequest`; 43/75 lines,
  fits the budget).
- `clone/state.py`: `source_kind` allowlist gains `"basic"`.

## Slice 3 — forum megagroups (large, ADR-0022)

### Destination

- New destination kind: private owned **forum megagroup** —
  `CreateChannelRequest(..., megagroup=True)` + `channels.ToggleForumRequest`
  to enable topics. Creation-marker recovery works verbatim (marker title,
  adopt-or-create-or-block).
- Destination shape check becomes kind-dependent: `_is_private_owned_broadcast`
  for linear sources (unchanged), a sibling `_is_private_owned_forum` for
  forum clones. Both live next to each other in `commands/clone.py`.

### Topic map (`clone/topics.py`, new module, ≤100 lines)

- Maps source topic id → destination topic id, persisted in state
  (`topic_map`, same JSON-object shape as `id_map`).
- **Topics are created lazily during sync**, not upfront at init: a source
  topic-create service message (which sync currently skips) triggers
  `channels.CreateForumTopicRequest` in the destination with the source
  topic's title and icon color/emoji, and records the mapping. This makes
  topics created after init work with no extra machinery, and keeps init
  mutation-minimal.
- The **General topic (id 1)** exists in every forum and is never created —
  it maps to the destination's own General topic.
- Messages arriving for a topic id with no mapping and no seen topic-create
  message (e.g. history started mid-topic because the create message was
  deleted): create the destination topic on first use from the source
  topic's current title (one `GetForumTopicsRequest` lookup), report it in
  the sync counters.

### Message routing

- A source message's topic comes from its reply header (`forum_topic` flag /
  `top_msg_id`; absent → General).
- `clone/replies.py` stops rejecting `forum_topic` headers for forum clones:
  `top_msg_id` maps through `topic_map`, inner reply ids map through
  `id_map` as today. A non-forum clone still rejects forum headers
  (fail-closed shape validation stays).
- Ordering: forum messages share the supergroup's single id space, so the
  existing single cursor oldest→newest already interleaves topics in exact
  source order. No ordering changes.
- Transport: hybrid attribution rules apply per ADR-0021.
  `ForwardMessagesRequest` accepts `top_msg_id` for topic targeting; if live
  testing shows forwards misroute topics, the fallback is reupload-with-prefix
  for forum sources (decide at implementation time, record in ADR-0022).

### State (`clone/state.py`, budget 150 → 170)

- New fields: `destination_kind` (`"broadcast" | "forum"`; legacy files
  default to `"broadcast"`) and `topic_map` (object, only meaningful for
  forum clones; defaults empty). Unknown values fail closed, no migrations —
  same pattern ADR-0021 used for `source_kind`.

## Testing

- Validation matrix additions: bot `User` accepted; `Chat` accepted;
  `Chat` with `migrated_to` rejected with the hint; deactivated `Chat`
  rejected; forum megagroup accepted; forum + non-forum destination shape
  checks.
- Topics: lazy creation from topic-create message; General never created;
  unmapped-topic first-use creation reported; `top_msg_id` mapping in
  replies; non-forum clone still rejects forum reply headers.
- State: `destination_kind`/`topic_map` round-trip, legacy-file defaults,
  fail-closed on unknown kind.
- Live acceptance is the release gate (user rule: visual, not test counts),
  one pass per slice:
  1. Clone a dialog with a real bot → order + both authors visible.
  2. Clone a small basic group → order + attribution, idempotent rerun.
  3. Clone an owned forum with ≥3 topics (incl. messages in General and one
     reply across a topic) → topics 1:1, every message in the right topic,
     order within the run, reply links work, rerun copies 0.

## Documentation changes

- CONTRACT.md §11: destination invariant rewritten ("destination type
  follows source kind"), accepted-source list updated, new sync counters
  for topic creation.
- New ADR-0022: forum destinations + topic map; records the lazy-creation
  decision, the state budget change, and the forward-topic-targeting
  fallback once tested live.
- ISSUES.md: CLONE-002 closed by this spec.
- MAP.md, PLAN.md, DEVLOG.md updated with the code they describe.

## Implementation order

TDD throughout; each task leaves the suite green and is independently
shippable. Slices land in order 1 → 2 → 3, one commit (or PR) per slice.

1. **Bot dialogs** — gate change + validation tests + CONTRACT line.
   Acceptance: bot dialog clones end-to-end in mocked tests; live check 1.
2. **Basic groups: gate + state** — `"basic"` kind, migrated/deactivated
   rejections, state allowlist.
3. **Basic groups: sync + profile** — attributed transport reuse,
   `GetFullChatRequest` profile branch. Acceptance: live check 2.
4. **State fields for forums** — `destination_kind` + `topic_map`,
   defaults, fail-closed tests.
5. **Forum destination** — creation + `ToggleForumRequest`, shape check,
   marker recovery for forum kind.
6. **`clone/topics.py`** — lazy topic creation, General handling,
   first-use fallback, counters.
7. **Forum message routing** — replies.py forum headers, transport topic
   targeting (resolve the forward-vs-reupload question live).
8. **Docs** — CONTRACT §11, ADR-0022, ISSUES/MAP/PLAN.
9. **Live acceptance 3** (forum) — then DEVLOG.

## Open questions

None blocking. One deferred-to-implementation check: whether native
forwards route into destination topics via `top_msg_id` (task 7 resolves
it; both outcomes are designed for).
