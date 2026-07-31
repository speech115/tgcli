## 2026-07-31 — Chart wayfinder map for local Telegram archive (Claude)

**Did:** charted a wayfinder map as GitHub issue #100 ("Wayfinder map: local
Telegram archive with search and transcription") with nine child tickets
(#101–#109) wired with native sub-issues and blocked-by dependencies. Fired
three parallel research subagents at the frontier tickets #101 (telecrawl
capabilities), #102 (prior integration artifacts on this machine), #103
(tgcli-native coverage gap). No production code changed.

**Decided:** owner settled four charting forks: destination is an ADR set
plus a scoped implementation plan (planning, not building); architecture
choice (telecrawl sidecar vs native tgcli store) waits for research facts;
refresh happens via scheduled foreground one-shot runs, preserving the
no-daemon rule; transcription is local (FluidAudio/Parakeet), batch backfill
plus per-sync. Private dialogs are the only default scope; groups/channels
are opt-in per explicit request. Resident daemons and any mutation path
through the archive are out of scope on the map.

**Learned:** the machine carries substantial prior telecrawl art —
`tools/agent-tooling` (telecrawl-archive/-fast, patches), the old
`tools/telegram` control-plane telecrawl_gap, and karpathy-kb research from
2026-05/06 — which #102 now audits so past lessons gate the new attempt.
The uncommitted 2026-07-30 PROPOSALS.md entry is the seed proposal; its
re-entry gates frame the plan's acceptance.

**Later same session:** all three research tickets resolved (findings on
local branches `research/telecrawl-capabilities`, `research/telecrawl-prior-art`,
`research/tgcli-archive-gap`; summaries on the tickets). Key facts: telecrawl
is a local-cache importer with no incremental sync, no API history backfill,
no transcription hook, and still no per-dialog scope — the exact primitive
whose absence caused the confirmed 2026-06-16 full-account reimport incident.
tgcli already covers acquisition (export/changes/media) but has zero search
store; export fidelity gaps recorded (#103). Owner then resolved #104:
**native tgcli archive store**, no sidecar; telecrawl ideas harvested, not
its binary.

**Also resolved #105 (storage/privacy):** archive lives at
`~/.local/state/tgcli/archive/<account>/` (0700, config-overridable, reported
by `tg store`); append-only history — edit revisions + deletion tombstones,
explicit purge/rebuild only; keep-forever retention; per-account schema with
primary-only archiving initially; no off-machine backup in v1 (encrypted
backup stays in the map's fog).

**Also resolved #106 (transcription):** separate foreground
`tg archive transcribe` command draining a DB queue (sync only downloads
media); transcripts in a DB table indexed into FTS5, no sidecar files;
backfill newest→oldest; local multilingual Parakeet with a Russian
acceptance check gating backfill; retryable/terminal failure taxonomy with
queryable `no transcript` marks; model+version stored to allow deliberate
re-transcription.

**Also resolved #107 (refresh/scope):** hourly launchd foreground one-shot
(delta sweep → media fetch → transcribe), clone-style per-run FLOOD_WAIT
budget, explicit rebaseline only; private 1:1 dialogs are a standing
auto-included category (future correspondents included), groups/channels
join individually via `tg archive add/remove/list`; no unlimited sentinel —
whole-account operations need an explicit guarded flag; failures surface via
`tg archive status`, staleness warnings in search, and a one-shot macOS
notification after repeated failed runs.

**Also resolved #108 (search surface):** CLI-only for owner and agents
(plain + `--json` per CONTRACT); friendly filter flags with a raw FTS5 MATCH
escape hatch; offline exploration first-class (`tg archive read` timeline,
edit/deleted history view); tg:// permalinks + live `tg read` handoff; BM25
with recency tiebreak, 50-row cap. Proof-of-value protocol and launchd
packaging graduated from the map's fog into #109's mandatory plan sections
(addendum comment posted there). TUI/web layer deliberately deferred to fog.

**Map completed same session (#109):** wrote and the owner accepted
**ADR-0068** (`docs/decisions/ADR-0068-local-archive-store.md`; index row
added) plus the seven-phase implementation plan
`docs/superpowers/plans/2026-07-31-archive-store.md` (fidelity
prerequisites → store/scope/selected backfill → proof-of-value HITL gate →
full private backfill + delta sync → media/transcription with Russian
acceptance check → search surfaces → launchd packaging and release).
PROPOSALS.md archive rows flipped to accepted/rejected with a resolution
note. Map #100 and all nine children are closed.

**Next:** commit/PR these docs, then hand the plan to executors starting at
Phase 0; Phase 2's proof-of-value acceptance gates everything full-account.
