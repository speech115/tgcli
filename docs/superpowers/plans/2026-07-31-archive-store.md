# `tg archive` — local archive store — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the local, read-only Telegram archive fixed by
[ADR-0068](../../decisions/ADR-0068-local-archive-store.md): per-account
SQLite+FTS5 store under `~/.local/state/tgcli/archive/<account>/`, private
dialogs auto-scoped, groups/channels opt-in, append-only edit/deletion
history, local transcription of voice/video notes, hourly foreground
one-shot refresh, CLI search/read/history surfaces. Wayfinder map #100 holds
the decision record (#101–#108).

**Architecture:** New `src/tgcli/commands/archive.py` (surface only) plus
`src/tgcli/archive/` package: `store.py` (schema, migrations, FTS5 — WAL
pattern imitating `clone/statedb.py`, own schema), `scope.py` (standing
private-dialog category + explicit allowlist), `sync.py` (backfill +
`tg changes`-cursor delta), `transcribe.py` (DB queue → local Parakeet),
`search.py` (query building, ranking). Acquisition reuses `read_ops` /
`message_to_dict` — one universal message shape; the archive stores that
shape, never a second form.

**Tech stack:** Python 3.12, Telethon 1.44, SQLite (stdlib `sqlite3`, FTS5),
argparse, pytest. Transcription via the local FluidAudio/Parakeet CLI
(invoked as a subprocess; no new Python deps).

## Global Constraints

- **ADR-0068 is the approved scope; nothing beyond it.** No daemon, no
  mutation path through the archive, no telecrawl binary, no TUI/web, no
  off-machine backup.
- **No unlimited sentinel.** Nothing in the surface may interpret `0`,
  empty, or absent as "everything". Whole-account/full-history operations
  exist only behind explicit long-form flags with confirmation
  (`--full-history --yes`-class), and every run has per-run ceilings
  (dialogs touched, messages fetched, bytes downloaded, wait budget).
  Boundary tests must prove the guard refuses, not just that the happy path
  works.
- **Account identity guard.** Every command verifies the store's bound
  account id against the live session before touching the DB; mismatch is a
  hard error (exit 2), never a merge.
- **Append-only history.** Edits insert revisions; deletions insert
  tombstones; `purge`/rebuild are the only local deletion paths and both
  are preview→commit gated like every tgcli mutation.
- **Read commands posture:** archive reads work under `--readonly` and on
  any `--session-role`; offline commands (`search`, `read`, `history`,
  `status`) must not open a network session at all.
- **Contract discipline:** CONTRACT section, guide pages, SKILL routing,
  MAP.md, FEATURES matrix, PROPOSALS.md row flip — in the same commits as
  the code they describe (ADR-0065 gates).
- **FLOOD_WAIT:** all RPC loops go through the clone cooldown/wait-budget
  seam (ADR-0045/0052); a run that exhausts budget stops cleanly, persists
  its checkpoint, and reports.

## Phase 0 — fidelity prerequisites (S)

Fixes from #103 that the archive depends on; each lands with tests and is
independently releasable.

- [x] `message_to_dict` permalink: resolve the entity so exported/archived
      messages carry a real `permalink`, not `null`.
- [x] `_media_kind`: add `video_note` (circles) as a distinct kind.
- [x] Cross-chat quote-replies: preserve the source chat id instead of
      collapsing to a bare message id.
- [x] Bulk `media download`: existing destination file → per-item skip
      (reported), not a batch-stopping `PolicyError`.

## Phase 1 — store, scope, selected-dialog backfill (M)

- [x] `archive/store.py`: schema v1 (`messages`, `revisions`, `tombstones`,
      `transcripts`, `scope`, `sync_state`, FTS5 index), WAL, migrations,
      0700 dirs, config-overridable root, account binding.
- [x] `tg archive init` (binds account, creates store),
      `tg archive add/remove/list` (groups/channels opt-in; private-dialog
      category is implicit and listed as such), `tg archive status`
      (freshness per dialog, counts, queue depth, last errors).
- [x] Backfill for an explicit dialog list: checkpointed, resumable,
      budget-capped history walk storing the universal message shape.
- [x] `tg store stats` reports the archive; cleanup never touches it.

## Phase 2 — proof-of-value gate (HITL, S)

- [x] Backfill 3–5 owner-selected dialogs (private + one channel).
- [x] Fixed task set: ~10 real owner search tasks, each attempted via
      `tg archive search` (may land minimally in this phase) and live
      `tg search`; record which wins and why.
- [x] **Gate:** owner accepts that the archive answers meaningfully better.
      Full-account backfill and later phases proceed only past this gate
      (PROPOSALS.md re-entry gate). Closed 2026-07-31 after full-depth
      remeasure (27 230 msgs, 19/19 in-scope ties, thin `archive search`
      + FTS v2); see `docs/devlog/2026-07-31-archive-phase2-pov-remeasure.md`.

## Phase 3 — full private backfill + delta sync (M)

**Entry conditions (owner / Fable, 2026-07-31):** land phases 0–2 + thin
search via PR into `main` first — do not stack Phase 3 on the open
feature branch. Full-account private backfill is a different FLOOD_WAIT
order of magnitude than the 5-chat PoV; keep per-run budgets and many
resumable runs (not one unbounded shot).

- [ ] Enumerate private 1:1 dialogs as the standing category; full
      backfill under per-run budgets across multiple resumable runs.
- [ ] `tg archive sync`: delta via the `tg changes` cursor held in
      `sync_state` (message_new/edit → rows + revisions; message_delete →
      tombstones; channel_activity → targeted per-channel catch-up for
      subscribed scope entries); new private dialogs auto-enter scope.
- [ ] **Live acceptance of append-only history:** in a test chat, edit then
      delete a message; after `tg archive sync`, observe a `revisions` row
      and a `tombstones` row. Schema alone is not proof — this is required
      before calling sync done.
- [ ] Gap handling: `differenceTooLong`-class gaps recorded loudly in
      `sync_state` + `status`; `tg archive rebaseline` as the explicit
      recovery command.
- [ ] Reconciliation sweep (light count comparison) on a documented cadence.
- [ ] Quick UX fix carried from Phase 2 PoV: `archive search --chat` must
      resolve private usernames/chatrefs, not only numeric `peer_id`
      (standing private scope has no `scope` row today).

## Phase 4 — media + transcription (M)

- [ ] Sync fetches voice/video-note media for in-scope dialogs into the
      store (budget-capped, idempotent via Phase 0 skip semantics).
- [ ] **Russian voice-note acceptance check** on the local Parakeet CLI —
      gates everything below.
- [ ] `tg archive transcribe`: drains the DB queue newest→oldest in bounded
      batches; stores text + model + version; indexes into FTS5;
      retryable-vs-terminal taxonomy; exhausted retries mark `no
      transcript` (queryable in `status` and `search`).
- [ ] **FTS rebuild must preserve transcripts:** `upsert_message` today
      rewrites the FTS row with `transcript=''` on edit ([store.py](../../src/tgcli/archive/store.py)).
      Harmless while transcripts are empty; before Phase 4 search relies on
      them, re-read the `transcripts` row (or equivalent) when rebuilding
      the FTS entry so an edit cannot silently drop transcript text from
      the index.

## Phase 5 — search and exploration surfaces (M)

Thin offline `tg archive search QUERY [--chat] [--limit]` + FTS schema v2
already shipped with Phase 2 (exact MATCH; `*` as raw escape hatch). Phase 5
completes the surface and closes the PoV rank gap (hit-count / top-k is not
yet identity with live — BM25 + recency is the bar here, not Phase 3).

- [ ] `tg archive search`: filter flags (`--chat --from --since --until
      --kind --transcripts-only`), raw FTS5 `MATCH` escape hatch, BM25 +
      recency tiebreak, `--sort date`, 50-row cap + paging, transcript-hit
      snippets, staleness warning, plain + `--json`.
- [ ] `tg archive read <chat>`: offline timeline around an id/date, no
      network.
- [ ] `tg archive history <chat> <id>`: revisions + tombstone view.
- [ ] Results carry `tg://` permalinks + ids for live `tg read` handoff.

## Phase 6 — scheduling, failure surfacing, release (S)

- [ ] `tg archive refresh`: the one-shot composition sync → media →
      transcribe with a shared budget, for launchd/cron use.
- [ ] Ship a documented launchd plist template (hourly) + guide page;
      installing it stays a manual owner step (no daemon, no auto-install).
- [ ] Failure surfacing: consecutive-failure counter in `sync_state`; after
      N failures the run fires one macOS notification via `desktop.py`.
- [ ] Docs + release: CONTRACT, guide, SKILL routing, MAP, FEATURES,
      PROPOSALS row flip, CHANGELOG; release per ADR-0038 after live
      acceptance (owner runs refresh + search on the real account).
