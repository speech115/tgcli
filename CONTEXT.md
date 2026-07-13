# Telegram Mirror Context

This glossary defines the Telegram topology and fidelity language used by the
tgcli mirror and its controlled laboratory.

## Chat Topology

**Basic group**:
A legacy Telegram group with its own history identity that may later migrate to
a supergroup.
_Avoid_: Small group, normal group

**Supergroup**:
A standalone many-member Telegram conversation without forum topics enabled.
_Avoid_: Group, megagroup

**Broadcast channel**:
A one-to-many Telegram chat whose posts may have a linked discussion group.
_Avoid_: Channel, feed

**Forum supergroup**:
A standalone supergroup with topics enabled, mirrored independently rather than
as the discussion surface of a channel.
_Avoid_: Forum, forum chat

**Channel discussion**:
The supergroup linked to a broadcast channel for comments; it may itself have
forum topics enabled.
_Avoid_: Comments chat, linked chat

**Topic**:
A named conversation partition inside a forum supergroup, including the General
topic.
_Avoid_: Thread

**Archived topic**:
A destination topic retained as closed history after its mapped source topic was
deleted.
_Avoid_: Deleted topic, dead thread

**Channel comment**:
A message in a channel discussion whose reply root is a specific channel post.
_Avoid_: Reply, discussion message

**Topology matrix**:
The required set of structurally distinct source and destination chat shapes
that must each have independent mirror evidence.
_Avoid_: Chat list, fixture list

**Source attribution**:
The visible indication of who authored a source comment or forum message,
without implying that the destination message was sent by that person.
_Avoid_: Impersonation, sender identity

**Unavailable parent**:
A source reply parent proven unobtainable after complete range evidence and a
targeted lookup, allowing an explicit non-threaded fallback.
_Avoid_: Missing reply, orphan

**Discussion topology parity**:
An exact match between the source and destination channel-discussion shape:
absent, plain supergroup, or forum supergroup.
_Avoid_: Discussion conversion, best-effort discussion

**Comment coverage**:
The complete accessible comment thread for every channel post included in the
authorized mirror range.
_Avoid_: Same-date comments, watcher-only comments

**Protection matrix**:
The required open/protected combinations across standalone chats and linked
channel-discussion pairs, with independent evidence for every cell.
_Avoid_: Protected-content test, one protected fixture

**Membership snapshot**:
A private local observation of visible source participants and roles that never
invites those people into the destination.
_Avoid_: Member copy, membership clone

**Membership completeness**:
Whether Telegram visibility and pagination prove that a membership snapshot
covers the full accessible list, with an explicit reason when they do not.
_Avoid_: Member count, assumed complete

**Membership fact**:
An append-only, evidence-backed observation that a visible source participant
joined, left, changed role, or changed public presentation.
_Avoid_: Member event, inferred departure

**Source lineage**:
The stable logical source spanning a legacy basic group and its migrated
supergroup successor while keeping their peer-scoped histories distinct.
_Avoid_: Merged peer, replacement chat

**Lab peer account**:
A dedicated secondary Telegram user identity controlled by the operator and
used only as the required participant in disposable basic-group fixtures.
_Avoid_: Test contact, lab bot

**Structural fidelity**:
Evidence that chat profiles, replies, pins, membership transitions, migration,
topics, comment roots, and cleanup relationships survive the mirror correctly.
_Avoid_: Message copying, media fidelity

**Content covering matrix**:
A lab design that runs structural sentinels in every topology/protection cell
and the full content suite once per distinct peer family and protection state.
_Avoid_: Sample messages, full Cartesian media matrix

**Authored fixture**:
A deterministic message or media item created inside a disposable lab source so
its source and destination semantics can be compared end to end.
_Avoid_: Sample, observed message

**Observation-negative fixture**:
A constructor that the lab can classify and render safely but does not create
because it is unavailable, unsafe, externally owned, or intentionally excluded.
_Avoid_: Supported fixture, ignored media

**Family-scoped capability**:
A content result that applies only to the peer family and protection state in
which it was measured, such as todo or live location in a supergroup.
_Avoid_: Global support, inherited result

**Scenario checkpoint**:
The durable, idempotent record of one lab scenario's current phase, created
peers, outbound operations, verdicts, and cleanup obligations.
_Avoid_: Progress flag, temporary state

**Compatible evidence**:
A completed scenario result whose topology, fixtures, code, dependency/schema,
account-role, and configuration fingerprints still match the aggregate run.
_Avoid_: Previous green, cached pass

**Aggregate acceptance**:
The final verdict built from every required compatible scenario result after all
cleanup obligations have independently passed.
_Avoid_: All command, summary pass

**Machine-green**:
The aggregate result proving all required protocol, topology, content, recovery,
and cleanup invariants without making a visual-quality claim.
_Avoid_: Accepted, looks correct

**Visual approval**:
An explicit human verdict on representative native Telegram views after the
machine checks pass and before their disposable peers are torn down.
_Avoid_: Screenshot pass, manual test

**Review lease**:
The bounded foreground interval during which visual-review fixtures may remain
alive before verified teardown becomes due.
_Avoid_: Pause, keep chats

**Review bundle**:
An opt-in, local, privacy-sanitized package of visual evidence, checklist facts,
and compatibility fingerprints with a bounded retention lifetime.
_Avoid_: Screenshot folder, lab export

## Fidelity

**Organic channel copy**:
A copied channel whose posts open native Telegram comment threads in the linked
destination discussion, preserving topic and reply relationships where present.
_Avoid_: Flat copy, comments dump
