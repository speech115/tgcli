# ADR-0015: Expand mirror topology support in evidence-gated stages

Status: accepted (2026-07-13).

## Context

ADR-0013 deliberately constrained the first production mirror to broadcast
channels and their linked discussions. The product target now also includes
standalone supergroups, forum supergroups, and legacy basic groups. Implementing
all of those topologies in one milestone would mix channel post mapping, comment
roots, forum topics, group replies, and basic-group migration behavior before
the laboratory has isolated their failure modes.

Telegram cannot create an owner-only basic group: `messages.createChat` requires
a vector of users to invite and may reject insufficient membership with
`USERS_TOO_FEW`. Owner-only supergroups are created through
`channels.createChannel(megagroup=True)`. Therefore basic-group source support
requires an explicit destination-shape decision rather than assumed topology
parity.

## Decision

The final production mirror scope includes broadcast channels with linked
comments, standalone supergroups, standalone forum supergroups, and legacy basic
groups. Delivery is staged and evidence-gated:

1. expand the controlled laboratory to cover every required topology;
2. implement broadcast channels with organic linked comments, including linked
   discussions that have forum topics enabled;
3. implement standalone supergroups and standalone forum supergroups;
4. implement legacy basic groups after migration and split-history behavior is
   proven in the laboratory.

A topology does not enter production routing merely because a neighboring
topology passed. Each stage requires its own unit, controlled-live, fidelity,
and cleanup evidence. One-to-one and secret chats remain outside this decision.

### Forum topic fidelity

Destination forums preserve the General topic and create a stable mapping for
every source topic before copying its messages. Topic titles, icons or colors,
and open/closed state track the current source state so the destination remains
native and usable. Every metadata transition also creates one idempotent,
append-only changelog fact.

A source topic deletion never deletes copied destination history. The mirror
closes the destination topic, marks it archived in native-visible metadata where
Telegram permits, and appends one deletion fact. Replies continue to resolve
through the stable topic and peer-scoped message mappings.

The first controlled-live standalone `forum.open` gate passed on 2026-07-13:
both disposable source and destination forums exposed General topic id 1,
created and read back a marked custom topic, and preserved direct parent plus
effective topic-root semantics for General and custom replies. Verified
teardown removed both peers and an independent exact-marker scan found zero
remaining dialogs. This is foundation evidence only; protected forum cells and
the full topic-transition matrix remain separate gates.

### Source author fidelity

Comments and forum messages use capability-based source attribution. When
Telegram permits native forwarding, the mirror preserves the official Telegram
forward header and its available source attribution. When native forwarding is
blocked or unavailable, reconstruction adds a compact textual attribution using
the source display name and public username when present.

The fallback never exposes a phone number, numeric user id, access hash, or
another private identifier. Missing public attribution is represented as an
explicit unknown or hidden source author rather than guessed identity. The
destination message is always technically sent by the mirror account; the
mirror never claims to impersonate the source user.

### Parent dependency fidelity

A comment or reply whose parent has no confirmed destination mapping enters a
durable dependency queue and is not flattened immediately. Once the parent is
confirmed, the child is dispatched as a native reply in the mapped destination
topic or channel discussion.

Fallback is allowed only after a complete source-range scan and targeted parent
lookup prove that the parent is deleted, inaccessible, unsupported, or outside
the authorized mirror range. The child is then copied in its correct topic with
an explicit unavailable-parent marker and a privacy-safe source reference. It
is never attached to an unrelated destination message or silently dropped.

### Channel discussion topology parity

The destination reproduces the source channel's discussion topology exactly:

- no linked source discussion creates no destination discussion;
- a plain linked discussion creates a plain private destination supergroup;
- a linked forum discussion creates a private destination forum supergroup.

Forum-discussion support is gated by a controlled live proof that the disposable
forum is returned as eligible for discussion linking, `channels.setDiscussionGroup`
accepts it, channel posts generate native discussion roots, and comment lookup
and reply routing work. Official schema compatibility alone is not acceptance
evidence. A failed gate blocks that topology; it never silently downgrades a
forum discussion to a plain group.

Controlled-live evidence on 2026-07-13 blocks linked forum discussions under
the pinned Telethon 1.44 schema layer 227. A forum created directly with
`channels.createChannel(forum=True, megagroup=True)` was absent from
`channels.getGroupsForDiscussion`. A plain megagroup was eligible and linked
successfully, but Telegram then rejected `channels.toggleForum` for that linked
group with `ChatDiscussionUnallowedError`. Both probes completed verified
teardown with no marked disposable peer remaining. The four `channel_forum.*`
cells therefore stay in the required matrix as explicitly blocked evidence;
they cannot enter mirror routing or be represented by a plain-group downgrade.
Standalone `forum.*` and `channel_plain.*` cells remain independent gates.

### Channel comment history boundary

Every channel post inside the authorized mirror range owns the complete
accessible history of its comment thread, regardless of individual comment
dates. Backfill pages and confirms that entire thread before declaring the post's
comment coverage complete, then the watcher follows new comments.

Comments rooted at channel posts outside the authorized mirror range are not
copied. Status reports their visible root and comment counts as excluded
coverage rather than silently implying completeness. Thread paging, dependency
release, and cursor updates are resumable; one large thread does not force other
posts to restart.

### Protected topology matrix

Controlled-live acceptance covers protection as an independent property of
every topology:

- basic group: open and protected;
- standalone supergroup: open and protected;
- standalone forum: open and protected;
- channel plus plain discussion: open/open, protected/open, open/protected, and
  protected/protected;
- channel plus forum discussion: the same four combinations.

Each scenario uses uniquely marked disposable peers and is created, verified,
and torn down before the next scenario. A scenario reports separate content,
structure, attribution, comment-root, topic, transport, audit, and cleanup
verdicts. Success in one protection combination never fills another matrix cell.

Controlled-live foundation coverage later on 2026-07-13 additionally passed
`supergroup.protected`, `forum.protected`,
`channel_plain.protected_open`, and eventually
`channel_plain.open_protected`, each with independent verified teardown and a
zero-result exact-marker scan. The first `open_protected` attempt hit
`FLOOD_WAIT 415`; a later bounded run confirmed both native comment chains and
cleaned successfully. `channel_plain.protected_protected` then hit
`FLOOD_WAIT 836` during provisioning. Its four peers were recovered after the
retry window, cleanup was green, and both account marker scans returned zero,
but the cell remains unaccepted. These outcomes do not collapse or infer the
missing cell.

The first controlled-live standalone `supergroup.open` foundation gate passed
on 2026-07-13. Both private owner-only source and destination supergroups
preserved a marked root, direct reply, and nested reply under exact server
readback, then completed verified teardown; an independent exact-marker scan
found zero remaining dialogs. This does not fill `supergroup.protected` or the
full structural/content matrix.

### Membership and destination ownership

Every destination group, supergroup, forum, discussion, and channel remains
private and owned only by the operator account. The mirror copies chat profile,
structure, and content but never invites source members, promotes administrators,
recreates bans, or sends membership notifications.

Visible source membership and role information may be captured only as a
private local membership snapshot owned by the mirror state. It is not posted
into the destination chat and is not treated as proof of a complete member list
unless Telegram visibility and pagination gates prove completeness. Membership
changes never authorize an external Telegram mutation.

A membership row contains only the canonical numeric user id for local
deduplication and author correlation, display name, public username when present,
source role, bot/deleted flags, and observation time. Snapshot metadata records
visible count, exported count, completeness, and an explicit incompleteness
reason. Phone numbers, biographies, access hashes, profile-photo bytes, and
other unrelated profile fields are forbidden.

Normal mirror status exposes only aggregate membership counts and completeness.
The row list is available only through an explicit local export destination; it
is never emitted implicitly to stdout, logs, audit records, or destination chats.

Membership storage keeps one normalized current row per visible source user and
an append-only fact stream for `joined`, `left`, `role_changed`, and
`name_changed`. Repeated observations of the same state are idempotent and do
not append duplicate facts or full snapshot copies.

A missing user becomes a verified `left` fact only after a complete paginated
membership scan and a targeted participant lookup confirm absence. Permission
loss, hidden members, cancellation, FloodWait, or another incomplete scan updates
no departure fact. Membership facts remain local and never trigger destination
membership mutations.

### Basic-group normalization and source lineage

A legacy basic-group source is copied into a private owner-only destination
supergroup. The destination records that normalization explicitly and never
invites a participant merely to manufacture a basic-group shape.

When the source basic group migrates to a supergroup, both peer ids belong to one
logical source lineage and one destination. The old basic-group peer remains the
history root; the successor supergroup becomes the current source peer. Message,
reply, scan, cursor, and changelog keys remain peer-scoped, and
`migrated_from_max_id` defines the history boundary. A mirror created from the
successor discovers the predecessor and derives the same lineage-root identity,
preventing a second destination.

Disposable basic-group source fixtures use one dedicated secondary Telegram user
account controlled by the operator, configured under an explicit tgcli alias
such as `lab-peer`. A bot or unrelated real contact is not valid evidence for
ordinary user-group behavior.

The lab manifest binds the operator and lab-peer aliases to expected distinct
canonical user ids before any mutation. Missing authorization, alias drift,
identity mismatch, or reuse of either account outside the marked scenario blocks
creation. The lab peer is invited only to the uniquely marked disposable source
fixture and never to a production destination.

Inside that fixture, the lab peer runs one bounded deterministic script: send a
marked text message and one generated media fixture, reply to a marked operator
message, receive and lose an admin role, leave, and be re-added. Every action has
correlated audit attempt/result records and verifies the expected peer, role,
message, and service update before advancing.

The first controlled-live basic-group foundation attempt on 2026-07-13 bound
the previously selected deployment alias to a distinct authorized ordinary user
and proved that the operator could resolve that exact account before mutation.
Telegram created the exact marked legacy group, but repeated fresh
`messages.getFullChat` reads contained only the operator; the bound lab peer was
not admitted. The runner therefore classified the attempt as
`basic_lab_peer_not_joined`, deleted the disposable group, and independent
marker scans on both accounts returned zero. `basic.open` and
`basic.protected` remain blocked; the lab must not switch accounts, modify
privacy/contact state, or infer ordinary-user membership from creation alone.

No action targets another dialog or uses personal content. Teardown must verify
that the disposable group is absent or definitively inaccessible from both
accounts. Ambiguous cleanup preserves the manifest and blocks acceptance instead
of claiming success or deleting an unverified peer.

### Structural fidelity matrix

A group or forum topology is not accepted from content transport alone. Its
controlled scenario must prove:

- title, about, avatar, and their source changes;
- text, media, album, direct reply, and a nested reply chain;
- pin and unpin;
- source message edit and delete represented through ADR-0013's append-only
  semantics;
- the bounded membership and role transitions defined above;
- disposable basic-group migration to a supergroup with one source lineage;
- the General topic and at least two custom topics;
- topic create, rename, icon/color change, pin, reorder, close, reopen, and
  delete under the topic-fidelity rule;
- messages and replies inside every topic;
- channel post, auto-forwarded discussion root, comment, and nested comment
  reply;
- complete teardown and an idempotent resume/re-run with no duplicate peer,
  topic, message, mapping, audit fact, or cleanup action.

Calls, boosts, reactions, and monetization are explicit structural exclusions.
They do not become supported through an unrelated green content or topology
result.

### Content evidence allocation

The expanded lab uses a covering design instead of repeating every media upload
in every linked protection cell:

- every protection cell proves routing with marked text, photo, album, and reply
  sentinels;
- the full content-kind matrix runs independently in open and protected basic
  groups, standalone supergroups, standalone forums, and broadcast channels;
- forum content is sent inside mapped topics, not only the General topic;
- linked plain/forum discussion cells focus on native comment roots, nested
  replies, attribution, and mixed channel/discussion protection;
- group and forum families additionally probe content rejected or degraded in
  broadcast channels, including todo and live location.

Evidence never crosses peer-family or protection boundaries. Existing broadcast
R1 evidence may satisfy the broadcast cells only when its schema, fixture set,
and verdict contract remain current; all other families require fresh evidence.

### Exact content-kind inventory

The full authored suite is explicit and versioned. For every open/protected peer
family it covers text with entities and captions; photo; generic document;
audio; voice; video; video note; animation; sticker; contact; static geo; venue;
dice; poll; webpage preview; and media groups. Sticker evidence includes static
WebP plus separate animated TGS and video WebM format cases when the account can
author them. A custom-emoji document is classified as its own observed subtype,
never as a generic document.

Media-group evidence covers a two-photo album with exactly one caption, a mixed
photo/video group, and a generic-document or audio group. It verifies constituent
order, captions, one stable source-to-destination mapping per item, and one
group-level relationship. A webpage fixture uses a stable URL and waits for
Telegram's asynchronous preview hydration; absence of a source preview is
inconclusive rather than a transport pass.

Document-backed reconstruction compares byte hashes where Telegram preserves
the payload and semantic attributes where Telegram or the client re-encodes it.
Photos use re-encode-tolerant semantic checks. Non-byte protected items are
reconstructed and compared semantically; they are not marked `not_applicable`
merely because they have no downloadable byte stream.

Todo and live location are family-scoped capability probes. Basic groups,
supergroups, and forums re-probe both independently in open and protected form;
forum fixtures run inside a mapped custom topic. Todo evidence covers creation
with stable item ids, append, complete/uncomplete, edit/remove, and a reply to a
specific item. Missing Premium capability is `blocked`, not an exclusion.
Broadcast rejection remains broadcast-only evidence.

Live-location evidence covers send, coordinate/heading update, and stop, and
requires `MessageMediaGeoLive` to remain live until the explicit stop. The
existing broadcast degradation to static geo remains a broadcast-only
unsupported result and never becomes a global exclusion.

Empty and unsupported media, unavailable stories, paid-media states, giveaways,
invoices, games, and custom emoji are observation-negative fixtures unless a
future safe authored scenario is accepted. Unknown future `MessageMedia`
constructors retain their constructor name and an explicit placeholder instead
of silently collapsing into generic unsupported content.

Service messages are action-specific structural evidence, not one transport
kind. The lab safely generates and verifies the actions already required by the
structural matrix, including chat/profile changes, participant transitions,
basic-group migration, pins, topic lifecycle, content-protection toggles, and
todo lifecycle. Every other action is preserved as an explicit constructor-level
placeholder until it has its own fixture and renderer rule.

Bot-owned games and invoices; giveaways, gifts, paid media, and payments;
stories and live stories; calls, conferences, and RTMP streams; reactions and
boosts; account/security actions; and scenarios requiring outsiders or real
money remain explicit exclusions. `MessageMediaVideoStream` is classified as
`video_stream` and excluded with live-story/group-call evidence; it must not fall
through to generic unsupported media.

Capability evidence records the exact Telethon version and Telegram schema
layer. Dependency or layer drift invalidates inherited content verdicts until
classifier and fixture compatibility checks pass.

### Resumable scenario execution

The expanded laboratory is a resumable scenario runner rather than one opaque
mutation script. Every protection/topology cell has a stable scenario key and a
durable checkpoint with these ordered phases: preflight, create, seed, mirror,
verify, teardown, and complete. The checkpoint records every created peer and
topic, prepared/dispatched outbound operation, per-domain verdict, remaining
cleanup obligation, and compatibility fingerprint.

An interrupted scenario resumes at the first unconfirmed phase. It reconciles
marked peers and operations before issuing another Telegram mutation, and never
creates a replacement merely because the previous response was lost. Ambiguous
creation, dispatch, or teardown blocks that scenario with its manifest intact.
Only an evidence-backed reconciliation may advance or retry it.

The runner exposes both a targeted scenario selection for development and a
serial aggregate run for acceptance. Targeted execution never upgrades the
overall product verdict by itself. Aggregate acceptance requires one compatible
result for every required matrix cell and domain, and independently requires all
teardown verdicts to be green. It reports missing, blocked, red, stale, and
cleanup-pending cells explicitly instead of averaging them into a pass.

Scenario compatibility includes the topology and protection key, fixture-schema
version, lab-code digest, mirror-code digest, Telethon version, Telegram schema
layer, account-role binding, and relevant configuration digest. Results with a
mismatched fingerprint are stale and cannot be borrowed.

Otherwise compatible controlled-live evidence expires 30 days after its
verified completion time. Aggregate acceptance immediately reruns mismatched
cells and reruns only expired compatible cells; it does not discard unrelated
fresh results. The report includes `completed_at`, `expires_at`, fingerprint,
and the exact stale reason for every cell. A clock that is unavailable or moves
backward makes age-based acceptance blocked rather than extending evidence.

Aggregate execution is serial by default to limit FloodWait risk and to keep
created-peer ownership and cleanup attribution unambiguous. A targeted run may
resume one existing scenario, but parallel Telegram mutation is not acceptance
evidence unless a later ADR proves isolated rate-limit and cleanup behavior.

### Machine and visual acceptance

Organic-copy acceptance has two independent verdicts. `machine_green` requires
the complete compatible scenario matrix and its protocol, topology, content,
recovery, and teardown invariants. `visual_approved` is an explicit human review
of native Telegram presentation. Neither verdict substitutes for the other, and
the aggregate report always exposes both.

Normal aggregate execution is unattended and tears down every scenario without
waiting for a person. A separate explicit visual-review mode creates one open
and one protected representative scenario for each destination topology only
after its focused machine checks are green. It presents private destination
references and a versioned checklist, then waits at a durable
`awaiting_visual_review` checkpoint before teardown.

The checklist covers channel profile and post presentation, native comment
entry points, auto-forwarded roots, nested replies, author attribution fallback,
plain versus forum discussion parity, General and custom topics, topic metadata
and lifecycle states, media groups and captions, protected reconstruction
labels, pinned content, edit/delete annotations, and basic-group normalization
disclosure. Approval is recorded per topology and protection state with reviewer
time and checklist version, never inferred from opening a link.

Visual review does not expose source membership rows or unrelated dialogs, and
the lab never posts the checklist into Telegram. A rejection records the failed
check and makes only `visual_approved` red; cleanup remains mandatory. The
retained visual-artifact policy remains a separate explicit decision.

Visual review uses a bounded foreground lease. The initial lease is 30 minutes
and an explicit heartbeat may extend it, but never beyond two hours from the
original `awaiting_visual_review` time. Approval, rejection, cancellation, or
lease expiry immediately advances the same foreground runner to teardown. Lease
timestamps and extensions are durable audit facts; opening a destination link
does not renew the lease.

tgcli does not introduce a background process for this timer. On a normal
signal, the foreground runner attempts teardown and preserves any unfinished
obligation. If the process or host dies, the manifest becomes `cleanup_due`; the
next lab invocation must reconcile and complete that teardown before it may
create another disposable peer. Until then, machine acceptance and visual
approval both remain non-green. The report never claims cleanup at the deadline
unless Telegram deletion/inaccessibility was actually verified.

Visual screenshots are opt-in through `--capture-review`. Without that flag,
the durable visual record contains only the checklist version and answers,
approve/reject verdict, reviewer time, scenario keys, and compatibility
fingerprints. Enabling capture creates a review bundle only under tgcli's local
state root; it is never written into the repository, stdout, Telegram, or a
shared directory.

Before persistence, every captured frame is constrained to the marked
destination chat and sanitized. The bundle forbids unrelated dialog chrome,
participant/member lists, phone numbers, local numeric user ids, access hashes,
notification previews, and account-switcher content. Fixture markers may remain
visible; public usernames appear only when the visual checklist specifically
tests the accepted attribution rule. A frame that cannot be proven scoped and
sanitized is discarded and makes capture incomplete rather than being retained.

Stored bundles use owner-only permissions and contain only sanitized images,
checklist facts, fingerprints, and hashes. Raw capture buffers are removed after
sanitization. Each bundle expires after 30 days. Because tgcli has no daemon,
every lab invocation performs retention cleanup before other work and an
explicit purge command is available; expiration is reported as `purge_due`
until deletion is actually verified. Expired artifacts never satisfy visual
approval even if filesystem cleanup has not yet run.

`visual_approved` is a promotion and presentation-release gate, not a routine
development gate. It is mandatory before a topology is enabled in production
for the first time. After promotion, it is required again when the destination
presentation, source-attribution fallback, topic/comment renderer, review
checklist, or relevant Telegram client/schema compatibility fingerprint changes.
It also expires under the same 30-day visual-evidence lifetime.

Focused development and recovery runs may proceed from `machine_green` without
waiting for a reviewer, but they cannot promote a topology or authorize an
affected release. Changes confined to non-presentation internals retain a fresh,
compatible visual result; the release report must show why its visual fingerprint
was unaffected rather than silently omitting the gate.

## Consequences

ADR-0013 remains the crash-safety and channel-mirror foundation, but its
general-group exclusion applies only to the first production milestone rather
than the final product boundary. The laboratory topology matrix is the gate for
each later stage; red or incomplete evidence blocks only the affected topology.
