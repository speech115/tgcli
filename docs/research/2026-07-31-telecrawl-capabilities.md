# Research: telecrawl capabilities and limits (openclaw/telecrawl)

- **Date:** 2026-07-31
- **Ticket:** speech115/tgcli#101 (parent: #100)
- **Repo researched:** https://github.com/openclaw/telecrawl
- **Method:** GitHub REST/GraphQL API (`gh api`) and raw file contents fetched
  directly from the `main` branch at the time of research (HEAD `57f85e8`,
  tag `v0.3.5`, 2026-07-27). No clone, no binary execution. DeepWiki has not
  indexed this repo (`Repository not found`), so all citations below are
  primary source: README, CHANGELOG, VISION.md, `go.mod`, CI workflow, and
  Go source files read via `gh api repos/openclaw/telecrawl/contents/<path>`.

## One-line gist

`telecrawl` is a local-first, MIT-licensed Go CLI that archives Telegram
Desktop `tdata` and native macOS Postbox data into a single SQLite+FTS5
database via bounded, merge-by-default **re-imports** (no continuous sync),
uses MTProto (`gotd/td`) only to opportunistically fill in missing cloud
media through an already-authenticated local session (never a login/backfill
path), records edits/deletions as an append-only revision log with
source-attributed tombstones, and has no transcription hook — voice/video
notes are archived as opaque media blobs, tagged but not transcribed.

## Data sources

- Primary sources are **local files only**: Telegram Desktop `tdata`
  (`~/Library/Application Support/Telegram Desktop/tdata`) and native
  Telegram-for-macOS Postbox group-container databases
  (`~/Library/Group Containers/6N38VWS5BX.ru.keepcoder.Telegram`). Source:
  README.md, "Data Paths" section.
- When no `--source` is given on macOS, `telecrawl` checks `tdata` first,
  then the native Postbox container automatically. Multiple local
  `account-*` Postbox databases are all imported, with chat/sender IDs
  account-scoped to avoid collisions (README, "Import" section).
- **There is an MTProto path, but it is not a backfill/login path.** `go.mod`
  pulls in `github.com/gotd/td v0.161.0` (a full Go MTProto client) as a
  direct dependency. It is used exclusively by
  `internal/telegramdesktop/postbox_remote.go` and
  `internal/telegramdesktop/flood_wait.go` to **re-fetch missing cloud
  media** for messages already found locally, by reusing the **existing
  native Telegram-for-macOS session's auth key** extracted from the local
  Postbox session storage (`postboxSessionStorage`, `NativeSessionForSource`)
  — it builds a `telegram.NewClient` with that captured `AuthKey`/`DCID` and
  calls `ChannelsGetMessages` / `MessagesGetMessages` / `MessagesGetHistory`
  to resolve one message and its media, then downloads it. This "does not
  launch Telegram or start a login/2FA flow" (README, media fetch section) —
  it can only act as far as an account already logged into the local
  Telegram-for-macOS app has synced/authorized, and it is scoped to media
  resolution, not message history backfill. There is **no code path that
  performs a general MTProto `messages.getHistory` backfill of chat history
  beyond what tdata/Postbox already cached locally** — history it can see is
  bounded by what the local Desktop/macOS client already downloaded, plus
  whatever a single-message `GetMessages`/`GetHistory` lookup resolves for
  already-known message IDs when `--fetch-media` is passed.
- Import is explicitly bounded: defaults are **latest 200 dialogs / latest
  500 messages per dialog** (README, "Import"); `--dialogs-limit 0
  --messages-limit 0` removes the bound, but the mechanism is still "read
  what's in the local cache."

## SQLite schema and FTS5 setup

Source: `internal/store/schema.go` (`schemaSQL`, `indexSQL`).

- Single SQLite database at `~/.telecrawl/telecrawl.db`, using
  `modernc.org/sqlite` (pure-Go, no cgo).
- Core tables: `chats`, `folders`, `folder_chats`, `topics`, `contacts`,
  `groups`, `group_participants`, `messages`, `message_revisions`,
  `sync_state`.
- `messages` is the central table: `event_id` (stable hash-derived
  identity, unique), `source_pk`, `chat_jid`, `msg_id`, sender info, `ts`,
  `edit_ts`, `text`, media fields (`media_type`, `media_title`,
  `media_path`, `media_url`, `media_size`), `metadata_type/title/url/json`
  (for link previews, polls, geo, service messages — decoded separately
  from binary media), `topic_id`, `reply_to_*`, `thread_id`, `forward_json`,
  `reactions_json`, `views`, `forwards`, `replies_count`, `pinned`, and a
  tombstone triplet `deleted_at` / `deletion_source` / `deletion_reason`
  present on essentially every table (chats, folders, folder_chats, topics,
  contacts, groups, group_participants, messages).
- **FTS5**: `create virtual table ... messages_fts using
  fts5(text, chat, sender, media)` — an external-content-free FTS5 table
  populated by explicit rebuild (`rebuildMessageFTS` deletes and
  re-inserts from `messages` joining `text + media_title + metadata_title +
  metadata_url`, `chat_name`, `sender_name`, `media_type`) rather than
  content-table triggers. Rows are pruned from FTS on tombstoning
  (`pruneDeletedMessageFTS`).
- Indexes cover `(chat_jid, ts)`, `(chat_jid, deleted_at, ts)`, `(chat_jid,
  msg_id)`, `(chat_jid, topic_id, ts)`, plus a unique index on
  `messages.event_id`.

## How edits/deletions are recorded

Source: `internal/store/tombstone.go`, `internal/store/snapshot_import.go`,
CHANGELOG `[0.3.5]`.

- Every canonical entity (chat, folder, folder_chat, topic, contact, group,
  group_participant, message) carries `deleted_at` / `deletion_source` /
  `deletion_reason` — an explicit, source-attributed tombstone, not a hard
  delete. Deletions of a parent (chat, topic, folder, group) are
  **propagated** to children via `propagateTombstones` (e.g. deleting a chat
  tombstones its messages with reason `parent_chat_deleted`).
- **Message identity is stable across imports** via
  `stableMessageEventID` = `sha256(chat_jid + "\x00" + message_id)`. This
  lets telecrawl recognize "the same message" across repeated imports.
- Edits and deletes are captured as an **append-only revision log** in
  `message_revisions` (`event_id`, `message_event_id`, `event_type` —
  `message_created`/`message_observed`/`message_edited`/`message_deleted`,
  `payload_json` snapshot, `event_at`, `observed_at`, `event_source`,
  `reason`, `predecessor_event_id` chaining revisions). On each import,
  `pendingMessageRevision` diffs the incoming message payload against the
  last stored canonical payload; if different, it writes a new revision
  (`message_edited` if `edit_ts` changed, `message_deleted` if the source
  reports the message deleted). `seedMissingMessageBaselines` backfills a
  baseline revision for any pre-existing archive rows so upgrades don't
  lose edit history going forward. This was introduced explicitly in
  v0.3.5 ("Preserve stable Telegram message identity and append-only
  revision events for baseline observations, observable message edits, and
  explicit deletes" — CHANGELOG).
- **Missing rows in a bounded import are not treated as deletions** — only
  an explicit delete signal from the source (or explicit `--restore`) tombstones
  a row (README, "Import"). This is a deliberate anti-data-loss design
  choice.

## Media handling

Source: `internal/telegramdesktop/postbox/postbox.go`,
`internal/telegramdesktop/postbox_remote.go`, README.

- Media detection is tag-based, decoded from Postbox's binary message
  format: `photo_or_video`, `file`, `music`, `web_page`,
  `voice_or_instant_video`, `gif`, `photo`, `video` (see `mediaTags` in
  `postbox.go`). Voice messages and video notes ("instant video" =
  Telegram's round video messages) share one combined tag
  (`voice_or_instant_video`) — telecrawl does not distinguish a plain voice
  note from a video note at this tag level; further detail comes from
  nested embedded-media metadata (`metadata_json`).
  message.
- **By default** telecrawl archives only media already cached locally by
  the Telegram Desktop/macOS client (`CachedMediaFor` matches resource IDs
  against local cache paths and picks the largest matching cached file).
- With `--fetch-media`, it additionally attempts to download Telegram
  **cloud** media that is not locally cached, via the reused MTProto
  session described above — this is described as "bounded best-effort":
  import stats report attempted / downloaded / missing / unavailable /
  timed out / errored counts. Repeat imports reuse already-archived media
  before retrying remote fetch.
- Archived media files are copied into `~/.telecrawl/media/` and are
  **never included in encrypted Git backups** — only metadata/paths are
  backed up (README, "Backup Security Model").
- Non-binary "media-shaped" content (link previews, polls, geo/live-geo,
  service messages, deleted messages) is stored as decoded
  `metadata_type/title/url/json` rather than as binary media rows, and only
  promoted to a real media row if Telegram actually returns a downloadable
  file for it.

## Voice/video-note handling and transcription

- No transcription hook exists anywhere in the repository. A GitHub code
  search across the repo for `transcri` returns no source-code hits (only
  incidental matches unrelated to transcription were absent entirely), and
  reading `postbox.go`/`metadata.go` shows only the `voice_or_instant_video`
  media tag classification, not any audio-processing or STT integration.
  Voice messages and video notes are archived like any other media file
  (binary blob + `media_type`/`media_title`/`media_path` in the `messages`
  row) — playback/transcription would be an external, downstream step, not
  something telecrawl does.

## Sync model: incremental vs. re-import

Source: `internal/store/store.go` (`sync_state` usage),
`internal/store/snapshot_import.go`.

- **There is no continuous/incremental sync daemon or cursor-based delta
  fetch.** Every `telecrawl import` run re-reads the (optionally bounded)
  local tdata/Postbox source in full and **merges** the result into the
  existing archive.
- The `sync_state` key/value table only stores bookkeeping —
  `last_import_at`, `source_path`, `source_path_canonical`,
  `source_identity` — used to (a) show `telecrawl status` and (b) **refuse
  a merge import if the Telegram account identity of the current source
  doesn't match the account already archived**, forcing an explicit
  `--restore` to switch accounts. There is no per-chat or per-message
  pagination cursor persisted for resuming an interrupted fetch — the
  "resume" semantics come from Telegram Desktop/Postbox's own local cache,
  not from telecrawl's own cursor state.
- Idempotency comes from the stable `event_id` hash plus a merge algorithm
  that compares incoming vs. stored payloads and only writes a new
  `message_revisions` row on an actual observed change — re-running import
  with unchanged data is a no-op at the revision-log level, but it is not
  an incremental fetch; it always re-derives from the full bounded local
  read.
- `--restore` (destructive) explicitly drops and replaces the entire
  archive with the current import; ordinary `import` is merge-by-default
  and preserves out-of-window rows and existing tombstones.

## Maintenance health

- **Active**: 8 tagged releases from `v0.1.0` (2026-05-08) to `v0.3.5`
  (2026-07-27), i.e. roughly weekly-to-biweekly releases over ~3 months up
  to the research date (2026-07-31); most recent commit to `main` is
  `57f85e8` on 2026-07-27 ("chore: open next unreleased section").
  Source: `gh api repos/openclaw/telecrawl/releases`, `/commits`.
- 7 human contributors (`steipete` dominant with 52 commits, plus
  `joshp123`, `vincentkoc`, `masonc15`, `sw1pp3r`, `nullyn`) plus
  `github-actions[bot]`. Source: `gh api repos/openclaw/telecrawl/contributors`.
- CI (`\.github/workflows/ci.yml`) runs `go vet`, `staticcheck`, `deadcode`,
  `gofumpt`, `gosec`, and a lint job (`golangci-lint`) on every PR/push to
  `main`; a separate `release-unified.yml` workflow handles signed,
  notarized, independently-reproducible-rebuild-verified releases
  (CHANGELOG `[0.3.4]`/`[0.3.5]` describe an elaborate supply-chain-hardened
  release pipeline: signed/notarized macOS binaries, byte-for-byte
  reproducible Linux/Windows rebuilds, verifier-gated Homebrew publication).
  `open_issues_count: 0` at time of research.
- 0 archived flag — repo is not archived. `size: 449` (KB, git repo size,
  small).

## License

- **MIT License** (`gh api repos/openclaw/telecrawl` → `license.name: "MIT
  License"`, and a top-level `LICENSE` file is present in the tree).
  Official macOS Homebrew binaries are additionally signed with an
  "OpenClaw Foundation Developer ID" and notarized by Apple; source builds
  and cross-platform snapshots are described as "credential-free" (README,
  CHANGELOG).

## Go toolchain needs

- `go.mod`: `module github.com/openclaw/telecrawl`, **`go 1.26.5`**
  (pinned exactly — CHANGELOG `[0.3.2]` notes this exact pin was needed "to
  fix reachable `crypto/tls` vulnerability GO-2026-5856 in official
  binaries," i.e. it's a security-driven floor, not an arbitrary pin).
  CI's `setup-go` action reads the version straight from `go.mod`
  (`go-version-file: go.mod`).
  Installable via `go install github.com/openclaw/telecrawl/cmd/telecrawl@latest`
  (no separate runtime needed at use-time — README: "No language runtime
  setup is required. `telecrawl` imports ... through the Go binary").
- Key dependencies: `modernc.org/sqlite` (pure-Go SQLite, no cgo),
  `github.com/gotd/td` (MTProto client, for the bounded remote-media path
  only), `filippo.io/age` (backup encryption), a private
  `github.com/openclaw/crawlkit` module (shared backup/mirror tooling used
  by `internal/backup`).

## Binary/reviewability

- Ships as a signed, notarized macOS Homebrew binary
  (`brew tap steipete/tap && brew install telecrawl`), a Docker image
  (`Dockerfile` present, README documents `docker run` usage mounting
  `tdata` read-only), or `go install` from source. Release provenance is
  unusually well-documented for a small tool: `docs/releasing.md` and an
  extensive CHANGELOG describe a "signed, notarized, independently
  verified" pipeline with reproducible non-Darwin builds and a distinct
  verifier step before Homebrew tap updates — this repo takes supply-chain
  review seriously and the CLI is small enough (`cmd/telecrawl/main.go` +
  `internal/{store,cli,backup,telegramdesktop}`) to audit directly from
  source rather than trusting the binary blindly.

## Relevance to tgcli

telecrawl solves a materially different problem than tgcli: it is a
**local-archive-of-local-cache** tool (tdata/Postbox → SQLite+FTS5), not a
live MTProto client for interactive session work. Its MTProto usage is
narrowly scoped to filling in missing media for messages the local client
already has, using a session the local Telegram-for-macOS app already
authenticated — it explicitly does not do account login, 2FA, or general
history backfill beyond the local cache. Its schema/tombstone/revision-log
design (stable `event_id` hashing, append-only `message_revisions`,
source-attributed tombstones, FTS5 rebuilt-not-triggered) is a candidate
reference design for any local-archive SQLite schema tgcli might build, but
its "no incremental sync, bounded merge-by-default re-import" model has the
same limitation any local-cache-only importer has: it cannot see Telegram
history the local Desktop/macOS client itself never downloaded.
