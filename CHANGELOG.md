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
