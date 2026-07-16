# ADR-0022: Clone forum sources into forum megagroups with a topic map

Date: 2026-07-16
Status: accepted
Builds on: ADR-0017 (clone), ADR-0021 (attributed sources).
Spec: docs/superpowers/specs/2026-07-16-clone-chat-types-2-design.md

## Decision

- The CONTRACT §11 invariant "destination is always a private owned broadcast
  channel" is replaced by "destination type follows source kind": forum
  megagroup sources clone into a private owned **forum megagroup**; every
  other source keeps the broadcast-channel destination. No `--dest-type`
  flag; no cloning into pre-existing groups.
- Topics map 1:1 through `topic_map` (source topic id → destination topic
  root id) in the per-clone state, created **lazily during sync**: a
  `MessageActionTopicCreate` service message creates the destination topic
  with `messages.CreateForumTopicRequest` (title + icon color/emoji); a
  message for an unmapped topic recovers the title via one
  `messages.GetForumTopicsByIDRequest` and creates it on first use. The General
  topic (id 1) always maps to the destination's General topic and is never
  created.
- Order fidelity is unchanged: forum messages share the supergroup's single
  id space, so the existing single oldest→newest cursor already interleaves
  topics in exact source order.
- Reply shapes: placement-only forum headers (no `reply_to_top_id`) are
  topic placement, not replies — they do not force the reupload transport
  and do not count as `reply_flattened`. Real in-topic replies map the
  parent through `id_map` and the topic through `topic_map`. Non-forum
  clones still reject forum reply headers fail-closed. Unsupported rows keep
  the existing early report-and-skip path before reply or topic routing.
- Transport: native forwards target topics via
  `ForwardMessagesRequest.top_msg_id`. This is verified by the mock/unit
  implementation; live topic-routing validation remains the Task 9 gate. The
  designed fallback — reupload for non-General topic batches — was not needed.
- Crash model: `create_topic` saves `topic_map` immediately after Telegram
  confirms the topic. A stale cursor after a saved mapping reuses the mapping
  and does not recreate the topic. If Telegram accepts creation without a
  usable confirmation, the resulting unmapped topic-create service row is an
  unexpected destination tail: the next sync blocks for manual repair before
  source scanning, audit, or another mutation.
- Budgets: new `clone/topics.py` ≤ 100 lines (owns forum destination shape,
  topic map, and batch confirmation extraction); `clone/state.py` raised
  150 → 170 for `destination_kind` + `topic_map`. `commands/clone.py`
  stays ≤ 400.

## Out of scope

Topic edit/close/hide propagation (`MessageActionTopicEdit` stays a skipped
service message), closed/hidden flags on created topics (v1 creates open),
cloning into existing groups, comments/watch.
