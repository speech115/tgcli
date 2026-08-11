# ADR-0068: Local archive store with FTS5 search and transcription

Date: 2026-07-31
Status: accepted (2026-07-31; refresh scheduling amended by ADR-0087)

## Context

The generic local mirror was rejected while Telegram's server-side history
and search sufficed (PROPOSALS.md, 2026-07-21 round). The owner has since
named a concrete scenario (PROPOSALS.md, 2026-07-30): Telegram is the
primary communication app, and "what did I write/read" must be answered
quickly across private chats and selected channels — including voice notes
and video circles, which live search cannot see, and including content the
server no longer shows (edits, deletions).

A wayfinder effort (map issue #100) resolved the open questions. Research
established that `openclaw/telecrawl` — the obvious sidecar candidate — is a
local Telegram Desktop/Postbox importer with no incremental sync, no MTProto
history backfill, no transcription hook, and no per-dialog scope primitive
(#101); the absence of that primitive caused the confirmed 2026-06-16
incident in the previous stack, where a one-channel refresh re-imported an
entire account (1,312 chats, 818K messages; #102). tgcli itself already
covers acquisition — resumable `tg export` backfill, one-call `tg changes`
delta (ADR-0063), bulk `tg media download` — but has no search store, no
FTS5, no edit/deletion history, and several fidelity gaps (#103).

## Proposal

A native, read-only, account-scoped archive owned by tgcli: a new
`tg archive` command family over a per-account SQLite store. No telecrawl
sidecar; telecrawl contributes ideas (coverage manifests, checkpoint/resume,
error taxonomy, operator gate), not its binary.

- **Store** (#105): `~/.local/state/tgcli/archive/<account>/` — SQLite/WAL
  following the `clone/statedb.py` pattern with its own schema: `messages`,
  `revisions` (append-only edit history), `tombstones` (deletions),
  `transcripts`, `scope`, `sync_state`, and an FTS5 index over message text
  + transcripts. Directory mode 0700; root overridable in `config.toml`;
  reported by `tg store stats`, never auto-cleaned. One DB per account with
  a hard account-identity guard on every run; primary account only at
  first. Keep-forever retention; `tg archive purge` (per dialog/message)
  and a full rebuild command are the only local deletion paths.
- **History semantics** (#105): the archive deliberately remembers what the
  server no longer shows. Edits become revisions, deletions become
  tombstones; nothing read is ever silently lost.
- **Scope** (#107): private 1:1 dialogs are a standing category — always in
  scope, including future correspondents. Groups and channels join only
  individually via `tg archive add/remove/list`. There is **no unlimited
  sentinel anywhere in the surface** (the 2026-06-16 lesson): whole-account
  or full-history operations require their own explicit long-form flag plus
  confirmation, and every run enforces per-run budget ceilings.
- **Refresh** (#107): hourly launchd-driven foreground one-shot — delta
  sweep on the `tg changes` cursor, then media fetch (voice/video notes in
  scope), then transcription — which exits. Clone-style per-run FLOOD_WAIT
  budget (ADR-0045/0052); cursor continuity checked every run; periodic
  light reconciliation; rebaseline only as an explicit command. Failures
  surface via `tg archive status` (freshness, queue depth, last errors),
  staleness warnings in search output, and a one-shot macOS notification
  (via `desktop.py`) after N consecutive failed runs. No resident process —
  ADR-0002's no-daemon rule stands.
- **Transcription** (#106): a separate foreground command
  (`tg archive transcribe`) drains a DB-defined queue (archived voice/video
  notes without transcripts) in bounded batches, newest → oldest. Local
  multilingual FluidAudio/Parakeet; transcripts stored in the DB with model
  name + version (enabling deliberate re-transcription) and indexed into
  FTS5. Retryable-vs-terminal failure taxonomy; exhausted retries mark the
  message `no transcript`, queryable. A Russian voice-note acceptance check
  gates the start of backfill.
- **Search and exploration** (#108): CLI only, for the owner and agents
  alike — plain output + `--json` under CONTRACT.md discipline.
  `tg archive search` with friendly filters (`--chat`, `--from`, `--since`/
  `--until`, `--kind`, `--transcripts-only`) plus a raw FTS5 `MATCH` escape
  hatch; BM25 ranking with recency tiebreak and `--sort date`; hard 50-row
  cap with explicit paging. `tg archive read <chat>` provides an offline
  timeline around a hit; a history view exposes revisions and deleted
  content. Results carry `tg://` permalinks and ids for a live `tg read`
  handoff.
- **Read-only boundary**: no send/edit/delete/forward/clone path through
  the archive. tgcli's live commands remain the only control route.

## Rejected

- **telecrawl sidecar** (also as an interim): no server backfill, no
  incremental sync, no transcription, no per-dialog scope, and an
  unreviewed Go binary contract coupled to the Python control plane (#104).
- A daemon or continuous event-driven mirror (ADR-0002; map out-of-scope).
- Mirroring server deletions locally — inverts the archive's core value;
  chosen instead: append-only history with explicit purge (#105).
- Sidecar transcript files beside media — two sources of truth (#106).
- Off-machine backup in v1 — local disk (+ Time Machine); revisit once the
  archive holds unique deleted/transcribed content (#105; map fog).
- A TUI/web viewer in v1 (#108; map fog).
- Deletion-by-absence inference — a bounded sweep proves nothing about
  what it did not see (wacli lesson; telecrawl agrees).

## Consequences

- New modules: `commands/archive.py` (surface) plus an `archive/` package
  (store schema/migrations, scope, sync, transcribe queue, search) —
  ADR-0034 ownership and line-ceiling rules apply.
- Prerequisite fidelity fixes from #103, shipped first: `permalink` no
  longer null in export paths reused by the archive; `video_note`
  distinguished as a media kind; cross-chat reply ids preserved; bulk media
  download gains skip-existing idempotency instead of hard-stop.
- The archive is the second sanctioned persistent store after clone state
  (ADR-0060); ADR-0002's "no state files" posture is already scoped to
  command invocations, not to explicitly owned stores.
- Release is gated by a proof-of-value acceptance: a fixed set of real
  owner search tasks answered better via the archive than via live
  `tg search` on selected dialogs, **before** full-account backfill.
- Doc duties in the implementing commits: CONTRACT section, guide pages,
  SKILL routing, MAP.md rows, FEATURES matrix, PROPOSALS.md row flip,
  CHANGELOG/release per ADR-0038.
- Implementation plan: `docs/superpowers/plans/2026-07-31-archive-store.md`.
