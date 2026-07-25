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

## [1.2.10] — 2026-07-25

### Fixed

- Clone comments phase: resolving the source discussion group at the start
  of `comments.sync_phase` no longer lets a Telegram access refusal
  (`ChannelPrivateError` and siblings) escape as an unhandled exception.
  Sync persists `comments: "unavailable"`, skips the comments leg and
  discussion roster, and exits 0 — the same honest marker init already
  wrote for an unreadable linked group (ADR-0023). A `FloodWaitError` on
  that resolve still propagates so the ADR-0045 cooldown arms. CONTRACT §11
  documents the path. Third site of the 1.2.8 attribution/roster class.

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
