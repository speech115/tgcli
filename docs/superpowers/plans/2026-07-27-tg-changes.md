# `tg changes` — daemonless change feed — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship FEED-001: a foreground command that returns Telegram change
events and a cursor, then exits. Design is fixed by
[ADR-0063](../../decisions/ADR-0063-tg-changes-design.md) (accepted
2026-07-27, amended): hybrid coverage (cursor-held channel subscriptions
with full events + `channel_activity` signals elsewhere), versioned opaque
cursor, `read`-shape event bodies, per-scope loud gaps, deletion
tombstones, `--wait N` with a fixed 2 s settle window, no state files.
Third release of the SQLite → session-roles → `tg changes` sequence —
**requires the session-roles release to be merged first** (branch from
`main` after it lands).

**Architecture:** New `src/tgcli/commands/changes.py` (command +
serialization) and `src/tgcli/changes_cursor.py` (cursor codec, pure
functions, no I/O). The command calls `updates.getState` /
`updates.getDifference` for the common tier and
`updates.getChannelDifference` per subscribed channel. Event bodies reuse
`src/tgcli/commands/read.py::message_to_dict` — one universal message
shape, no second form. The cursor is the only state and it lives with the
caller (ADR-0002 posture).

**Tech Stack:** Python 3.12, Telethon raw functions
(`functions.updates.*`), argparse, pytest. No new dependencies.

## Global Constraints

- **ADR-0063 is the approved scope; nothing beyond it.** No `--follow`,
  no NDJSON streaming, no sqlite event mirror, no story viewers, no
  `read_marker`. Dropped update classes are **counted in `skipped`**,
  never silently discarded.
- **Nothing silent.** Missing/malformed/wrong-version cursor → exit 2
  (never a full-history replay); gap → explicit `gap` object with scope
  and honest `recover` (creation history recoverable via `read
  --after-id` hint; edit/delete history stated as lost); subscription
  changes only via explicit `--peer`/`--drop-peer`; a new subscription is
  baselined at current `pts` with a stderr note (no replay).
- **Cursor codec contract:** opaque to the caller, versioned internally
  (`v1:` + urlsafe-base64 payload: common `pts`/`qts`/`date`/`seq` +
  `{channel_id: pts}`). Property tests (Hypothesis): encode/decode
  round-trip; any tampered/truncated string decodes to exit 2, never an
  exception escape.
- **Boundary tests assert exact request types** (AGENTS.md): the
  `GetDifferenceRequest` / `GetChannelDifferenceRequest` /
  `GetStateRequest` classes and their argument types, per subscribed
  channel, including the `pts_total_limit`/`limit` choices made. A
  permissive fake is not proof.
- **Read command posture:** works under `--readonly` and on any
  `--session-role` (ADR-0062); no audit mutation rows (it mutates
  nothing); stdout carries exactly one JSON document, notes on stderr.
  Exit 0 on success **including gaps**; exit 2 usage/cursor errors; the
  ADR-0053 error envelope for the rest.
- **Contract discipline:** CONTRACT section, `docs/FEATURES.md` `updates`
  row flips from `excluded` with the shipped rationale, guide page, SKILL
  routing (tgcli skill), MAP.md — in the same commits. Integrator owns
  version/CHANGELOG (ADR-0058). Run `./scripts/gate.sh` before every
  commit.

## Slice 1 — cursor codec + `--init`

- [ ] **Codec (TDD + Hypothesis):** `changes_cursor.py` — encode/decode,
  version check, subscription add/drop as pure functions. Adversarial:
  empty, `v0:`, corrupt base64, negative pts, duplicate/unmarked peer ids
  (peer ids follow the CONTRACT marked form, `-100…`).
- [ ] **`tg changes --init [--peer P …]` (TDD):** resolves peers, calls
  `GetStateRequest` + per-channel current `pts`
  (`GetChannelDifferenceRequest` with a baseline or the channel's
  `full`-info pts — pick one, pin it in a boundary test), returns
  `{"events": [], "next_cursor": …}`. `--init` with an existing cursor →
  exit 2 (one way to do each thing).

## Slice 2 — the difference loop and events

- [ ] **Common tier (TDD, boundary):** `GetDifferenceRequest` paging until
  final state; map new/edited messages → `message_new`/`message_edit`
  (bodies via `message_to_dict`; truncated bodies marked, never padded),
  deletes → `message_delete` tombstones, `updateChannelTooLong`-class →
  `channel_activity` for unsubscribed channels; everything else →
  `skipped` counters.
- [ ] **Subscribed channels (TDD, boundary):** `GetChannelDifferenceRequest`
  per subscription; same event mapping; per-channel new `pts` into the
  cursor. A subscribed channel also present in the common difference must
  not double-report (dedupe rule, test it).
- [ ] **Gaps (TDD):** `differenceTooLong` → scope `"common"`;
  `channelDifferenceTooLong` → scope `<peer id>`, rebasing only that
  channel; gap + partial events in one document is legal and tested.
  `recover` carries the per-chat `read --after-id` hint.
- [ ] **`--peer`/`--drop-peer` on a regular call (TDD):** add baselines at
  current pts + stderr note; drop removes from cursor; dropping an
  unsubscribed peer → exit 2.

## Slice 3 — `--wait`, wiring, docs

- [ ] **`--wait N` + settle (TDD with a fake clock):** long-poll up to N
  for the first event, then a 2 s settle bounded by the remaining
  deadline; without `--wait` return immediately, no settle. Deadline is
  never exceeded — pin with the fake clock.
- [ ] **CLI wiring:** parser (`--cursor`, `--init`, `--peer` repeatable,
  `--drop-peer` repeatable, `--wait`), dispatch, `--json` default-on
  posture per CONTRACT conventions; adversarial flag-combination tests
  (`--init --cursor`, `--wait` without cursor, empty `--peer`).
- [ ] **Docs:** CONTRACT section (document shape, exit codes, gap/skipped
  semantics, the settle constant), FEATURES `updates` row, guide page
  with the poller pattern (`--session-role job` loop), SKILL routing,
  MAP.md, ISSUES.md FEED-001 closing entry recording the shipped shape.

## Slice 4 — live acceptance (release gate)

Owner present; test account + disposable test channel; results in the
session devlog. **No tag before this slice is green.**

- [ ] `--init --peer <test channel>`; send → edit → delete a message in
  the channel (owner action); `tg changes --cursor …` returns
  `message_new` (full body), `message_edit`, `message_delete` tombstone;
  cursor advances across calls.
- [ ] Activity in an **unsubscribed** channel surfaces as
  `channel_activity`; `--peer` mid-stream subscribes it; `--drop-peer`
  unsubscribes.
- [ ] `--wait 30` on the job role while `tg send` runs on the primary
  (the FEED-001 contention scenario — must not conflict); settle window
  batches a quick burst of two messages into one document.
- [ ] Gap attempt: replay a stale cursor (kept from before a bulk of
  traffic / after a long dormancy) and verify the gap document; if
  Telegram will not produce `differenceTooLong` on demand, record the
  attempt and the mock-level coverage honestly in the devlog.
- [ ] Report gate + CI output; integrator merges, bumps version/CHANGELOG,
  tags after acceptance; FEED-001 closes.
