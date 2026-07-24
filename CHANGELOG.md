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

## [1.2.0] — 2026-07-24

### Added

- **Account lifecycle closes** — `tg accounts login` (QR by default, phone +
  confirmation code fallback), `tg accounts show`, and `tg accounts remove`
  (ADR-0042). Cloud password via native dialog or `--password-stdin`, never
  argv. Login attempts live under `logins/` and promote into
  `sessions/<alias>.session` only after Telegram confirms. Owner-declared
  minor milestone.
- `store stats` / `store cleanup` learn the `logins/` bucket and report
  `session_backups` (`.bak`); cleanup reaps expired attempts only.

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

[1.2.0]: https://github.com/speech115/tgcli/compare/v1.1.3...v1.2.0
[1.1.3]: https://github.com/speech115/tgcli/compare/v1.1.2...v1.1.3
[1.1.2]: https://github.com/speech115/tgcli/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/speech115/tgcli/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/speech115/tgcli/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/speech115/tgcli/releases/tag/v1.0.0
