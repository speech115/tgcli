# Changelog

All notable changes to tgcli. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[semver](https://semver.org/) over the CLI automation contract in
[docs/CONTRACT.md](docs/CONTRACT.md) — flags, JSON shapes, and exit codes.
JSON changes are additive-only. Features and fixes ship as patch releases;
the owner declares minor milestones.

Rationale for each entry lives in the ADR it names
([docs/decisions/README.md](docs/decisions/README.md)); session-level detail
lives in [docs/DEVLOG.md](docs/DEVLOG.md).

## [2.0.2] — 2026-08-09

`tg media download` now accepts story links — public `t.me/<user>/s/<id>` and
private `t.me/c/<id>/s/<id>` — resolving them via `stories.getStoriesByID`.
Video stories download the main document by default; `--codec
{h264,h265,hevc,av1}` selects an encoding from the document's `alt_documents`
by its `video_codec` attribute (missing encoding is exit 4). Story sources are
single download only (bulk flags are exit 2); the result keeps the media
shape with a `story:` source label and an additive `codec` field.

### Changed

- Download story media in tg media download (ADR-0076) (#161)

## [2.0.1] — 2026-08-09

This release adds server-side voice transcription (`tg transcribe`, Premium)
and the read-only allowlist / docs groundwork behind it (story media downloads
land in 2.0.2). `tg transcribe <chat> <id>` waits up to `--timeout` for the
async transcription result; a non-voice message is exit 4 and a missing
Premium subscription exit 2. `upload.getFile` is now reachable through `tg api`
for raw story-media pulls, and the launchd archive-refresh template bounds its
wall time (`--max-runtime 3000`). Process: the risk-tiered lanes and the
release-preparation script landed (ADR-0073/0074).

Rationale: ADR-0073, ADR-0074, ADR-0075, ADR-0076.

### Changed

- Add tg transcribe for server-side voice transcription (ADR-0075) (#160)
- Track the #96 runtime-schema check as a deferred proposal (#159)
- Allowlist upload.getFile as a read-only API method (#158)
- Bound wall time for scheduled archive refresh in the launchd template (#157)
- Add the complexity-reset rule and the release preparation script (ADR-0074) (#155)
- Split the change process into risk lanes (ADR-0073) (#154)
- Land the wayfinder research notes and tidy the repository (#153)

## [2.0.0] — 2026-08-02

Major because exit code 5 changes meaning for an identical trigger; see
"Changed" below. ADR-0072.

### Added

- An account-wide request governor (ADR-0072) that paces and gates every
  Telegram request the tool makes, replacing the clone-only flood
  containment of ADR-0045/0052. Cooldowns are per request type and shared
  across processes through a SQLite ledger, so a penalty drawn by one
  command refuses the commands that would draw it again — and only those.
  A flood arms the type from the server's own `retry_after`; a refusal is
  local, costs zero RPCs, and exits 5. Each cooldown is probed once at half
  its wait, so a limit lifted early clears itself without operator action.
  CONTRACT §1, §4, §5.1, §9, §11, §13.
- `--max-runtime <sec>`, an explicit wall-clock cap for long runs.
  Exhausting it is a normal stop: exit 0, `stop_reason: "wall_clock_cap"`,
  checkpoint intact, and a resume pointer where the command keeps a cursor.
  A rolling 100-distinct-peers/24 h breadth budget stops the same way with
  `stop_reason: "breadth_budget_exhausted"`.
- `tg doctor` reports the governor directly: `checks.governor_cooldowns`
  maps each cooling request type to its deadline, and
  `checks.governor_degraded` says when the ledger could not be opened
  (reads fail open, so protection degrades without blocking). A cooldown is
  reportable state, not a failure — it does not set `ok: false`. `doctor`
  is the one command exempt from the governor, so it works precisely when
  everything else refuses.
- The invocation journal gains `governed_sleep_ms` and `request_count` on
  runs that issued governed requests, `retry_after` / `request_type` /
  `provenance` on a flood-related exit, and `stop_reason` on a normal stop.
  CONTRACT §9.

### Changed

- **Breaking.** A scheduled `archive refresh` that wakes into a partial
  cooldown now exits **0** with `stop_reason: "cooldown_deferred"` and a
  `deferred` list, where it previously exited 5. It does what the free
  request types allow and reports the rest as deferred — a success that
  deferred work, not a failure to alert on. A consumer polling exit 5 as
  "the account needs to wait" must read the journal or JSON fields instead.
  CONTRACT §4.
- `--timeout` is a hang detector, not a job bound: deliberate governed
  sleep no longer counts against it. Long-running commands (`media`,
  `export`, `clone init|sync|refresh`, `archive refresh`) keep no implicit
  deadline and are bounded only by an explicit `--timeout` or
  `--max-runtime`. CONTRACT §1.
- `clone init`'s preview no longer echoes `account_flood`. Active cooldowns
  are per request type in the governor's ledger and are surfaced by
  `tg doctor`. CONTRACT §12.

### Removed

- The ADR-0045 account-scoped cooldown record and the ADR-0052 short-wait
  machinery (`SHORT_WAIT`, `WAIT_BUDGET`, `WaitBudget`, `FloodGate`,
  `with_cooldown`). A clone-sync flood no longer waits 60 s in the
  foreground under a 180 s per-process budget; it arms the governor's
  per-type cooldown and exits 5, or sleeps the wait out when it fits the
  remaining `--max-runtime`. Both ADRs are now plainly superseded.

## [1.2.25] — 2026-07-31

### Added

- Archive Phase 6 (ADR-0070): `tg archive refresh` composes delta sync,
  media acquisition, and local transcription into one bounded foreground
  pass suitable for a scheduler; an account-level failure streak with one
  best-effort macOS notification after three consecutive failed runs,
  reported under `archive status`; and a manual hourly launchd plist
  template that tgcli never installs or supervises. CONTRACT §13.

### Fixed

- A private dialog first seen through the delta feed was recorded as
  having no further history, so `archive backfill --private` skipped it
  permanently and its past correspondence never arrived. New peers now
  start with `more=true`, and `--private` only skips dialogs that actually
  walked their history; a single recovery run refills them.
- Archive media downloads gained their own attempt counter and terminal
  `no_media` status (schema v6), so a permanently unavailable voice note
  stops being retried every run. Item-level media and transcription
  failures, and `FLOOD_WAIT` backpressure, no longer feed the refresh
  failure streak — previously one dead item could latch the notification
  and silence every later outage.

## [1.2.24] — 2026-07-31

### Added

- Archive Phase 5 (ADR-0068, ADR-0069): `tg archive search` gains
  `--from`, `--since`, `--until`, `--kind`, `--transcripts-only`,
  `--sort`, and `--page` with BM25 ranking, a recency tiebreak, a 50-hit
  cap with paging, and transcript-aware snippets; new offline
  `tg archive read` (timeline centred on an id or date) and
  `tg archive history` (revisions plus tombstone). Hits carry per-peer
  `tg://` handoff links. Query composition lives in the read-only
  `archive/explore.py` module (ADR-0069). CONTRACT §13.

### Fixed

- `archive search --from` matched names through SQLite's ASCII-only
  `lower()`, so a Cyrillic display name silently returned no hits; the
  comparison now folds case for Unicode. Malformed raw FTS5 queries are
  usage errors (exit **2**) instead of runtime failures.

## [1.2.23] — 2026-07-31

### Added

- Archive Phase 4 (ADR-0068): `backfill` and `sync` acquire queued `voice`
  and `video_note` media into the account-local `media/` directory
  (idempotent atomic publish; `sync --max-media` budget, backfill a fixed
  50), and `tg archive transcribe` drains the queue offline through the
  local Parakeet CLI newest→oldest — storing text plus model and version,
  indexing transcripts into FTS5, and marking exhausted retries
  `no_transcript`, queryable in `status` and `search`. Schema v4; FTS
  rebuilds now preserve transcripts across message edits. CONTRACT §13.

## [1.2.22] — 2026-07-31

### Added

- Archive Phase 3 (ADR-0068): `tg archive backfill --private` (bounded
  standing-category enumeration with `--max-dialogs`, skip-complete
  resume), `tg archive sync` (changes-cursor delta: new/edit → rows +
  revisions, deletes → tombstones, scoped `channel_activity` catch-up
  under a `--max-events` message budget, rotating light reconcile) and
  `tg archive rebaseline`. Difference events are always applied in full
  before the cursor advances; peer-less deletes never touch `-100…`
  channel peers. Private `--chat` resolution in `archive search`.
  Live-accepted on `main` (revision + tombstone observed through sync).
  CONTRACT §13.

## [1.2.21] — 2026-07-31

### Added

- Local archive store (ADR-0068 phases 0–2): per-account SQLite/WAL under
  `archive/<alias>/`, standing private scope + explicit group/channel
  allowlist, selected-dialog `tg archive backfill`, offline
  `list`/`status`, and thin offline `tg archive search` (exact/raw FTS5
  MATCH, `--chat`, 50-cap) with FTS schema v2 (`unicode61` + ё→е fold).
  CONTRACT §13. Phase 2 PoV gate closed on live `main` after full-depth
  remeasure.

### Fixed

- Export/media fidelity prerequisites for the archive: real `permalink`s,
  distinct `video_note` kind, cross-chat reply peer preservation, and bulk
  `media download` skip-existing / per-item NotFound continuation
  (ADR-0068 Phase 0 / issue #103).

## [1.2.20] — 2026-07-30

### Added

- Voice-message playback state in the universal message JSON as the additive
  `voice_played` field (ADR-0066, issue #97).
- Runtime identity diagnostics in `tg doctor`, plus the documented runtime
  boundary that keeps session access on the checkout's pinned Telethon
  environment (ADR-0067, issue #96).

## [1.2.19] — 2026-07-27

The three sequential releases planned in PR #91, landed as one integrator
release after independent Spec+Standards review of each PR.

### Added

- Named session roles beside the primary (ADR-0062): `accounts login|show|
  remove --role`, the global `--session-role` flag with no implicit
  fallback (missing/unauthorized role exits 3 with remediation), per-role
  `doctor` checks, and `role` recorded in mutation audit rows and the
  invocation journal (CONTRACT §1/§5.1/§9/§10).
- `tg changes` daemonless update feed (ADR-0063): opaque `v1:` cursor,
  `--init [--peer …]` baselines, bounded `GetDifference` /
  `GetChannelDifference` polls, `message_new` / `message_edit` /
  `message_delete` / `channel_activity` events, loud gap reporting, and
  `--wait N` with a fixed settle (CONTRACT §12).

### Changed

- Clone state moved from clone JSON files to SQLite/WAL behind the same
  `CloneState` seam (ADR-0060): dirty-tracked O(1) saves, a one-time JSON
  import that renames the old file to `.json.imported`, `tg clone
  export-state` as the rollback path, `clone status` reporting
  `schema_version` + `integrity`, and `store stats` breaking out `.db` /
  WAL / SHM / `.imported` files (CONTRACT §11). Duplicate-destination
  mappings now fail loudly as policy errors instead of being silently
  resolved, and WAL/SHM sidecars are created `0600`.

## [1.2.18] — 2026-07-27

### Fixed

- Clone reupload/snapshot forward prefixes resolve a `fwd_from.from_id`
  channel title from the entity Telegram already shipped with the source
  message (`message.forward.get_chat()` / accompanying chats) when a
  standalone `get_entity` raises `ChannelPrivateError` / left-peer
  refusal — so private origins still get `Переслано от <title>` instead
  of bare `Переслано` (issue #80, ADR-0064).

## [1.2.17] — 2026-07-27

### Changed

- `tg api` constructor objects in `--params` now also accept
  `ChatAdminRights` and `ChatBannedRights`, so `channels.editAdmin` /
  `channels.editBanned` can be built without inventing a non-existent
  `Input*` form (CONTRACT §6). Still reject every other non-`Input*` TL
  constructor; the participants-filter exception is unchanged.

## [1.2.16] — 2026-07-26

Stabilization release: no new commands. Every item below started from a
reproducing test.

### Security

- State directories are created **and repaired** at `0700` and state files at
  `0600` — the state root, `sessions/`, `logins/`, `previews/`, `audit.jsonl`,
  `invocations.jsonl`, and the Telethon `.session` (tightened before
  `connect()`, on both the normal and the login paths). Permission repair is
  fail-open: a mode that cannot be fixed never breaks the command. `doctor`
  gains the additive `session_perms_ok` check (covering `.session` and
  `.session.bak`) and its permission checks now reject group bits, not only
  other (ADR-0004/0043).
- Two account aliases whose session names collide case-insensitively are
  rejected at config load **and** on the write path, so a case-insensitive
  filesystem can no longer make two Telegram accounts share one
  authorization file (ADR-0042).
- Human and plain output strip control characters from Telegram-controlled
  names, and clone progress lines can no longer carry `\r` or escapes via a
  filename (CONTRACT §2/§8). The `export subscribers` CSV formula guard now
  also catches tab-, CR-, and space-prefixed payloads.
- Destructive gates cannot be abbreviated: `allow_abbrev` is off, so `--c`
  is no longer accepted for `--confirm` nor `--w` for `--write`. Raw-write
  audit records now name `user_id`, so an `editAdmin` row identifies the
  account and not just the room (ADR-0010/0011).

### Fixed

- `--format html` fails closed instead of silently truncating the message.
  Unterminated markup (`if a<b then c`) and unsupported tags
  (`List<int> is generic`) were dropped by the parser, so the body approved
  in the preview was not the body Telegram received — on `send`, `edit`, and
  `draft set` alike. Astral character references no longer desynchronize
  UTF-16 entity offsets (ADR-0030).
- A short FloodWait now stalls **every** parallel upload worker through a
  shared per-run gate, instead of one worker sleeping while its siblings kept
  issuing RPCs and each charged the same wait to the budget
  (ADR-0045/ADR-0052).
- A crash between the pin RPC and the state save no longer latches
  `pin_occupied` forever: a destination pinned to exactly the message this
  run intended to pin is adopted as recovered work (ADR-0055).
- `clone refresh` mirrors sync's `source_kind` branch, so a megagroup clone
  can no longer be rewritten with broadcast-style `Переслано от` attribution;
  and an album whose lead cannot be proven is excluded as
  `album-lead-unknown` rather than promoting a survivor and prefixing the
  wrong live message (ADR-0050/ADR-0054).
- A FloodWait blocking the transient poll retract is disclosed as
  `retract_failed` instead of vanishing into a generic exit 5, and the
  retract audit row is no longer written before the RPC that may not happen
  (ADR-0048).
- The reupload cache proves completeness with a marker file, so a download
  killed mid-stripe can no longer be reused as if it were the real media.
  Documents keep their still-image thumbs, chosen by object rather than by a
  list index that raises `IndexError` on animated stickers (ADR-0052).
- Failures reach the caller as contract data: `--timeout` expiry is a
  `TIMEOUT` envelope, an untranslated network or RPC failure is a `RUNTIME`
  envelope (traceback only under `-v`), argument misuse is `USAGE`, a closed
  stdout pipe exits quietly, and a run killed by a signal still journals an
  honest row (ADR-0053, CONTRACT §2/§4/§9).
- An unknown chat exits 4 instead of a traceback on `send` preview/commit,
  `edit --commit`, and `delete --commit`, which passed the raw chat string to
  Telethon and therefore always crashed on a numeric dialog id.
- Malformed input fails closed rather than crashing: a non-dict clone state,
  an invalid `id_map` or `retry_not_before`, a malformed `accounts` table, a
  truncated-UTF-8 export tail, a non-dict media-download state, and naive
  `expires_at` stamps in previews and login attempts.
- Recorded clone peers that can no longer be reached (deleted, left, banned)
  map to the documented exit 2 everywhere, including the discussion-group
  destination and `init`'s recovery path.
- Persisted cooldowns are clamped, so a host clock running ahead can no
  longer brick every clone command for that account; the QR login loop is
  rate-bounded against the same skew; and `clone sync`/`refresh` enforce the
  cooldown before the `get_me` RPC, as CONTRACT §11 promises.
- `media download` survives a destination on another filesystem (EXDEV),
  restarts cleanly after an interrupted transfer left a partial with no
  state, and marks a parallel transfer as unresumable instead of wedging it.
- `tg api` resolves numeric peer aliases through `chatref` (they were parsed
  as phone numbers and never resolved), accepts every documented
  `channels.getParticipants` filter, and requires `--confirm` for the
  irreversible `messages.migrateChat` and `channels.convertToGigagroup`.
- `tg batch` rejects the flag and enum combinations the interactive CLI
  rejects, instead of silently returning a different result set.
- `clone status` accepts both the raw and the `-100`-marked source id.
- State renames are durable (parent-directory fsync) and go through the one
  sanctioned atomic writer; `store` survives a preview consumed mid-walk and
  no longer deletes a staged login whose lock is held.

### Known and not fixed

- `login_state.promote()` swaps the previous session to `.bak` and moves the
  staged session in without a single atomic step, so a crash inside that
  window can leave the account with a backup and no live session (audit
  finding af-14). Recovery is manual (rename the `.bak` back) or a fresh
  `accounts login`. Closing it properly needs a documented recovery path
  rather than a wider rename, so it is deliberately deferred rather than
  patched under a release.

### Changed

- `commands/clone.py` shrank from 1596 to 1199 lines: the cooldown gate and
  RPC seam moved to `clone/cooldown.py`, the reupload transfer mechanics to
  `clone/reupload.py`, and the init peer/profile work to
  `clone/init_peers.py`. Behavior is unchanged — the suite passes with the
  same count before and after each move.
- `uv lock --check` runs in CI and in `scripts/gate.sh`, which `uv sync
  --frozen` never verified, and `check-architecture.py` fails loudly when a
  listed state-writing module is missing instead of skipping it.

## [1.2.15] — 2026-07-25

### Added

- `tg clone refresh SOURCE` / `tg clone refresh SOURCE --commit PREVIEW_ID`
  backfills missing ADR-0050 `Переслано от <label>` body prefixes onto posts
  whose destination body is still byte-identical to the unprefixed source;
  poll snapshots, native re-forwards, and the discussion leg are excluded;
  each edit is `EditMessageRequest` text+entities only with a
  `clone-refresh-prefix` audit beforehand (ADR-0054). Preview-scan and commit
  RPCs share sync's `_with_cooldown` path; `--commit` fail-closes when the
  preview's account/`source_peer_id`/`id_map` pairs no longer match live state.

## [1.2.14] — 2026-07-25

### Added

- `clone sync` on a broadcast destination carries the source's pinned post
  once the posts leg is exhausted (`more: false`): silent
  `messages.UpdatePinnedMessage`, never overriding an existing destination
  pin, never mirroring an unpin. Sync JSON gains `pinned` with status
  `set` / `unchanged` / `unmapped` / `occupied`. Forum destinations omit
  the key. Destination occupancy is read once in the clone's lifetime;
  later completing runs answer from state (ADR-0055 Track A).

### Fixed

- Striped photo download (`download_striped`, used by clone reupload and
  `tg media download --parallel`) now selects the largest `PhotoSize` by
  byte count instead of trusting Telegram's unsorted `sizes[-1]`
  (ADR-0055 Track B / decision 6). This does **not** explain or resolve
  the `[икона]` audit's five smaller photos (source messages 15/33/35/58/81):
  all five are under the 512 KB striped threshold and never took this path.
  Live measurement of those five (Task 4) remains owner-gated.

## [1.2.13] — 2026-07-25

### Changed

- `clone sync` interleaves the posts and comments legs in fixed 50-batch
  windows (ADR-0051, amending ADR-0023's ordering clause only): posts×50,
  then comments up to the first source-group anchor whose channel post is
  newer than the posts cursor, then the next posts window. An interrupted
  sync leaves a coherent prefix — posts with their discussion — instead of
  every post and a silent group. `--limit N` counts batches across both
  legs, so a limited run may return comments where it previously returned
  only posts. An unmapped cross-leg comment parent beyond the posts cursor
  defers (does not send) rather than flattening permanently; a parent
  behind the cursor and absent from the map still flattens. Cursors, state
  shape, and JSON fields are unchanged. CONTRACT §11 documents the
  interleaving.

## [1.2.12] — 2026-07-25

### Changed

- `clone sync` waits out a short `FloodWaitError` (≤ 60 s) once in the
  foreground under a 180-second per-process wait budget, then retries the
  same request; a second failure, a longer wait, or a spent budget still
  persists the cooldown and exits 5 (ADR-0052; amends ADR-0045's exit-on-
  flood clause only).
- Reupload downloads persist under `clones/<clone_id>-media/` and are reused
  when name and byte size match; the directory is removed after a successful
  send and left on disk after a failed one (ADR-0052).
- `store stats` reports a `clone_media_cache` bucket; `store cleanup
  --confirm` removes abandoned `clones/*-media/` directories without touching
  clone state JSON (ADR-0052). A cache is abandoned only once its newest file
  is older than one hour, so cleanup can never delete the media a running
  `clone sync` is downloading — the other buckets get that protection from
  their own TTL.

## [1.2.11] — 2026-07-25

### Fixed

- With `--json`, a failing command writes the error envelope to stdout as the
  run's single JSON document and mirrors the identical line to stderr
  (ADR-0053). Callers that capture stdout no longer see an empty success on
  exit 1–5.
- `clone sync` comments-phase progress no longer sticks the denominator at
  `~?` after the posts leg: `SyncProgress.phase()` clears the resolved-total
  flag so the next leg re-queries once.

## [1.2.10] — 2026-07-25

### Fixed

- Clone comments phase: resolving the source discussion group at the start
  of `comments.sync_phase` no longer lets a Telegram access refusal
  (`ChannelPrivateError` and siblings) escape as an unhandled exception.
  Sync persists `comments: "unavailable"` (and clears leftover
  `discussion_cursor` / `discussion_id_map` so state stays loadable),
  skips the comments leg and discussion roster, and exits 0 — the same
  honest marker init already wrote for an unreadable linked group
  (ADR-0023). A `FloodWaitError` on that resolve still propagates so the
  ADR-0045 cooldown arms. CONTRACT §11 documents the path. Third site of
  the 1.2.8 attribution/roster class.
- Clone story snapshots: `get_entity` for the story author now treats any
  Telegram access refusal as a missing label (`неизвестен`), matching
  `attribution._resolve`; previously only `ValueError` was caught.
- Stale-docs sync against live 1.2.x: `CONTRACT.md` header tracks the package
  version (was still `0.1` draft); §6 allowlist count is **40** (ADR-0010);
  §10 documents `tg accounts list` JSON/TSV (ADR-0042). `FEATURES.md` marks
  `contacts` and `folders` as `wrapped`; README maintenance line says v1.2.
- Unauthorized-session `ConfigError` points at `tg accounts login` (with
  `accounts import` only as the old-stack path), not "phase 6 / authorize
  manually".

## [1.2.9] — 2026-07-25

### Added

- `clone sync` reports progress on stderr in every mode, including `--json`:
  a line per batch with the running count against the approximate source
  total, `comments` / `roster` phase lines, and a ~5 MB mark during each
  reupload transfer naming the file and direction (ADR-0049). Plain lines
  only; stdout stays exactly one JSON document. Informative, not contract
  data — silence with `2>/dev/null`. The approximate total costs one
  `messages.getHistory(limit=0)`, resolved lazily so a sync with nothing new
  spends no extra RPC.

### Changed

- The chunk-cadence progress callback is one shared seam in `transfer.py`
  (`PROGRESS_EVERY_CHUNKS`) used by `media download`, the striped download,
  and the clone reupload legs; `upload_parts` now reports bytes too
  (ADR-0043/0049).

## [1.2.8] — 2026-07-25

### Changed

- Clone posts leg: a reposted, single-message broadcast batch that can prove
  its original in the clone's linked source discussion group (reachable,
  not `noforwards`; exactly one group message matches `fwd_from.from_id`
  and `fwd_from.date`; text, formatting entities, and media — spoiler flag
  included — identical to
  the post) now forwards
  that original into the destination channel instead of prefixing
  `Переслано от <label>`, so the destination carries Telegram's own header;
  such a batch counts as `forwarded`, not `reuploaded`, in the sync JSON
  transport counts. Albums and snapshot-mode posts are excluded. Any
  unproven case keeps the 1.2.7 text-prefix fallback (ADR-0050).

### Fixed

- Clone attribution no longer aborts a sync when Telegram refuses to name a
  peer. `author_of` and `forwarded_author_of` caught only `ValueError`, so a
  post forwarded from a channel the account cannot access raised
  `ChannelPrivateError` and killed the run; both now treat any such refusal
  as a missing label and fall through the ladder. A `FloodWaitError` still
  propagates and arms the cooldown (ADR-0045).
- Clone roster: the same refusal shape no longer escapes the participant
  snapshot. `collect` caught only `ValueError` around the discussion-group
  resolve, so a source group that turned private crashed `sync` *after* the
  messages had already copied; it now records `"unavailable"` with the error
  name, and a FloodWait there records `"deferred"` without arming either
  cooldown, as ADR-0024 already promised.

## [1.2.7] — 2026-07-25

### Changed

- Clone posts leg: a protected (reupload/snapshot) channel post that is
  itself a forward gains a truthful Russian `Переслано от <label>` body
  prefix built only from `fwd_from` (`from_id` / `from_name` /
  `post_author`, or bare `Переслано`); native forward path unchanged
  (ADR-0050).

## [1.2.6] — 2026-07-24

### Changed

- Poll clone snapshots: honest "распределение по вариантам недоступно"
  when voters exist without a breakdown; anonymous open non-quiz polls
  briefly vote+retract to capture percentages (own vote subtracted)
  (ADR-0048). Sync JSON gains additive `poll_votes`.

## [1.2.5] — 2026-07-24

### Changed

- Protected-channel clone reupload transfers media chunks with parallelism 4:
  striped `iter_download` for large files and concurrent
  `SaveFilePartRequest` / `SaveBigFilePartRequest` uploads (ADR-0047).
  Message/batch ordering and cursor discipline are unchanged. FloodWait
  during transfer still exits 5 with cooldowns armed.

## [1.2.4] — 2026-07-24

### Added

- `clone init --commit` mutes tool-created peers forever and files them into
  the Telegram folder `Clone`, reporting
  `ergonomics: {muted, folder}` (ADR-0046). Best-effort: failures warn on
  stderr and never fail init.

## [1.2.3] — 2026-07-24

### Added

- `clone init --no-comments` creates a posts-only clone with
  `comments: "disabled"` (no discussion peer, no comment sync) (ADR-0045).
- Init preview JSON gains `peers_to_create` and `account_flood` so callers
  can see peer-creation cost and flood posture before commit (ADR-0045).

### Changed

- A FloodWait on any mutating clone RPC now also arms an account-scoped
  cooldown under `clones/account-<user_id>.json`. Later `init --commit` /
  `sync` for every clone of that account exit 5 locally until the deadline
  (ADR-0045). Per-clone `retry_not_before` remains; either scope blocks.
  Roster floods still arm nothing (ADR-0024).

## [1.2.2] — 2026-07-24

### Fixed

- `clone init` re-runs no longer re-upload an unchanged source avatar onto
  the destination (and discussion group). The copied source photo id is
  recorded in clone state and the copy is skipped until the source avatar
  changes — previously every re-run posted a "photo updated" service
  message and spent three mutating calls per peer.

## [1.2.1] — 2026-07-24

### Changed

- Clone destinations are titled `[Clone] {source_title}` (and discussion
  groups `[Clone] {display_name}`) so tool-created peers are visually
  distinct from their sources in the dialog list (ADR-0044).
  `source.title` / `CloneState.source_title` stay unprefixed. Re-running
  `clone init` on an existing clone applies the prefix idempotently
  without creating peers.

## [1.2.0] — 2026-07-24

### Added

- **Account lifecycle closes** — `tg accounts login` (QR by default, phone +
  confirmation code fallback), `tg accounts show`, and `tg accounts remove`
  (ADR-0042). Cloud password via native dialog or `--password-stdin`, never
  argv. Login attempts live under `logins/` and promote into
  `sessions/<alias>.session` only after Telegram confirms. Owner-declared
  minor milestone.
- Telegram Settings → Devices identifies regular and login sessions as
  **tgcli** with the package version, instead of architecture-only labels such
  as `arm64`.
- `store stats` / `store cleanup` learn the `logins/` bucket and report
  `session_backups` (`.bak`); cleanup reaps expired attempts only.

### Fixed

- `store cleanup` no longer treats a login attempt truncated mid-write as
  expired (mtime fallback mirrors previews); attempt JSON is written atomically.
- QR login for a new alias backs up an existing destination session and
  requires `--force` when that orphan file is still authorized.
- Headless phone confirmation requires `--code` / `--code -` instead of
  blocking on stdin; empty codes are rejected before `sign_in`.
- `accounts show` for a missing session creates no stray `.lock` file.

## [1.1.3] — 2026-07-23

### Changed

- **`tg doctor` offline by default** — local checks always; live authorization
  only with `--connect`. Offline `authorized` is `null`; plain status may be
  `unknown` (ADR-0040).
- New local checks `preview_perms_ok` / `audit_perms_ok`. Installations with
  legacy `0644` previews will report `ok: false` until tightened; `doctor`
  prints the remedy (`tg store cleanup --confirm`) to stderr.

## [1.1.2] — 2026-07-23

### Added

- **Local state inventory and cleanup** — `tg store stats` / `tg store cleanup`
  (ADR-0040). Offline-only. Cleanup reaps spent/expired previews behind
  `--confirm`; never touches the audit log or sessions. New previews are mode
  `0600`.

## [1.1.1] — 2026-07-23

### Added

- **Message drafts** — `tg draft set|show|clear|list`. `set`/`clear` go through
  preview→commit with an internal observed-state snapshot, rechecked immediately
  before save; Telegram offers no conditional-save token, so the final read→save
  race remains explicit. `show`/`list` are typed read ops (batch + readonly).
  Own draft JSON object, not a message shape. No `draft send` (ADR-0039).

## [1.1.0] — 2026-07-23

Agent-correspondence release: everything an agent needs to read a dialog
precisely, act on single messages, and pull data out in bulk. All changes are
additive — 1.0.0 invocations keep working.

### Added

- **Message mutations under preview→commit** — `edit`, `delete`, `forward`,
  `mark-read`; `send` gained `--reply-to`, `--file`, `--caption`, `--topic`,
  `--silent`. Commits go through a retryable `random_id` confirmation, so a
  network failure mid-send cannot silently duplicate a message (ADR-0028).
- **Outgoing formatting** — `--format {plain,md,html}` on `send` and `edit`;
  `custom_emoji` surfaced on the universal message JSON (ADR-0030).
- **Precise reads** — `read --after-id/--before-id/--since/--until/--topic`
  with page metadata; `message --context N`; richer agent-facing message
  fields (ADR-0028).
- **Global search** — `search --all`, `--from`, `--since` across dialogs
  (ADR-0028).
- **Discovery and inbox** — `resolve` (username/phone/id → stable peer),
  `contacts list|search`, `thread` (reply chains), `media manifest`,
  `dialogs --unread-only/--kind`, `dialog pin|unpin`, `mark-unread`,
  `info --full` role/rights preflight (ADR-0029).
- **`doctor`** — per-account health: username, authorized, premium
  (ADR-0028).
- **Data plumbing** — `mutual-chats`, `dialog archive|mute`, incremental
  `export messages`, bulk media download with filters, and a read-only
  `tg batch` for many reads in one session (ADR-0032).
- **`export subscribers`** — full broadcast subscriber export, plus stories
  reads (ADR-0031).
- **Agent skills routing** — `docs/agents/` (issue tracker, triage labels,
  domain-doc discovery) and the `tgcli` skill contract (ADR-0033).
- **CI gates** — ruff, architecture, pyright, pytest, and coverage
  (ADR-0027/0034).

### Changed

- **Clone preserves quote replies.** Replies carrying a quote resolve
  natively when the quoted peer is reachable, and degrade to a rendered
  fallback (`Переслано от:` + quoted body) when it is not, instead of
  failing the batch (ADR-0036/0037).
- **Internal seams** — the CLI entry monolith split into
  `parser`/`preflight`/`dispatch`/`cli` (ADR-0035), and reads moved behind
  one typed read-operation registry shared by the interactive CLI and
  `tg batch` (ADR-0034). No user-visible behavior change.

### Fixed

- `tg api` no longer crashes on a bare `Bool` RPC result (ADR-0010).
- `dialogs --limit 0` behavior, `info --full` rights preflight, and a
  file-send TOCTOU window closed with verified snapshots (ADR-0028).
- Mute pre-audit validation and the `tg batch` FLOOD_WAIT exit code
  (ADR-0032).

## [1.0.0] — 2026-07-17

First release. Stateless CLI over Telethon: dialogs, reads, search,
two-step `send`, media download, export, `tg api` read-only passthrough,
and `tg clone` for channels, non-forum supergroups, and private dialogs.
The project entered maintenance mode on the same day (ADR-0026).

[2.0.2]: https://github.com/speech115/tgcli/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/speech115/tgcli/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/speech115/tgcli/compare/v1.2.25...v2.0.0
[1.2.25]: https://github.com/speech115/tgcli/compare/v1.2.24...v1.2.25
[1.2.24]: https://github.com/speech115/tgcli/compare/v1.2.23...v1.2.24
[1.2.23]: https://github.com/speech115/tgcli/compare/v1.2.22...v1.2.23
[1.2.22]: https://github.com/speech115/tgcli/compare/v1.2.21...v1.2.22
[1.2.21]: https://github.com/speech115/tgcli/compare/v1.2.20...v1.2.21
[1.2.20]: https://github.com/speech115/tgcli/compare/v1.2.19...v1.2.20
[1.2.19]: https://github.com/speech115/tgcli/compare/v1.2.18...v1.2.19
[1.2.18]: https://github.com/speech115/tgcli/compare/v1.2.17...v1.2.18
[1.2.17]: https://github.com/speech115/tgcli/compare/v1.2.16...v1.2.17
[1.2.16]: https://github.com/speech115/tgcli/compare/v1.2.15...v1.2.16
[1.2.15]: https://github.com/speech115/tgcli/compare/v1.2.14...v1.2.15
[1.2.14]: https://github.com/speech115/tgcli/compare/v1.2.13...v1.2.14
[1.2.13]: https://github.com/speech115/tgcli/compare/v1.2.12...v1.2.13
[1.2.12]: https://github.com/speech115/tgcli/compare/v1.2.11...v1.2.12
[1.2.11]: https://github.com/speech115/tgcli/compare/v1.2.10...v1.2.11
[1.2.10]: https://github.com/speech115/tgcli/compare/v1.2.9...v1.2.10
[1.2.9]: https://github.com/speech115/tgcli/compare/v1.2.8...v1.2.9
[1.2.8]: https://github.com/speech115/tgcli/compare/v1.2.7...v1.2.8
[1.2.7]: https://github.com/speech115/tgcli/compare/v1.2.6...v1.2.7
[1.2.6]: https://github.com/speech115/tgcli/compare/v1.2.5...v1.2.6
[1.2.5]: https://github.com/speech115/tgcli/compare/v1.2.4...v1.2.5
[1.2.4]: https://github.com/speech115/tgcli/compare/v1.2.3...v1.2.4
[1.2.3]: https://github.com/speech115/tgcli/compare/v1.2.2...v1.2.3
[1.2.2]: https://github.com/speech115/tgcli/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/speech115/tgcli/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/speech115/tgcli/compare/v1.1.3...v1.2.0
[1.1.3]: https://github.com/speech115/tgcli/compare/v1.1.2...v1.1.3
[1.1.2]: https://github.com/speech115/tgcli/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/speech115/tgcli/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/speech115/tgcli/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/speech115/tgcli/releases/tag/v1.0.0
