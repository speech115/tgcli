# ADR Index

One row per ADR; this is the canonical index (moved from MAP.md by
[ADR-0026](ADR-0026-maintenance-mode.md)). Adding an ADR means adding its
row here in the same commit (AGENTS.md rule, extending
[ADR-0007](ADR-0007-docs-discipline.md)).

| ADR | Decision | Status |
|-----|----------|--------|
| [0001](ADR-0001-python-telethon.md) | Python 3.12 + Telethon, not Go/gotd, not TDLib-first | accepted |
| [0002](ADR-0002-cli-first-stateless.md) | Stateless CLI core; no daemons; MCP is a v1 non-goal | accepted |
| [0003](ADR-0003-output-contract.md) | stdout=data, stderr=human, fixed exit codes | accepted |
| [0004](ADR-0004-accounts-and-sessions.md) | SQLiteSession per account + file lock; import from old stack | accepted |
| [0005](ADR-0005-safety-model.md) | Reads free; writes preview→commit + audit; runtime flags not baked profiles | accepted |
| [0006](ADR-0006-media-tdlib-fallback.md) | TDLib as optional fallback backend | superseded by ADR-0009 |
| [0007](ADR-0007-docs-discipline.md) | MAP + ADR + DEVLOG as mandatory agent workflow | accepted; the ADR trigger narrowed to the ADR-0073 full-lane list |
| [0008](ADR-0008-raw-api-passthrough.md) | `tg api` raw TL passthrough and write-path safety | superseded in part by ADR-0010 (read classification only) |
| [0009](ADR-0009-tdlib-deferred.md) | TDLib deferred: no backend in v1; phase 3 Telethon-only; evidence-gated PoC re-entry | accepted |
| [0010](ADR-0010-raw-api-read-allowlist.md) | `tg api` phase-2 explicit default-deny read allowlist | accepted |
| [0011](ADR-0011-audit-write-failure-policy.md) | Audit persistence fails closed before any mutation | accepted |
| [0012](ADR-0012-invocation-journal-and-verbose-diagnostics.md) | Local invocation journal and opt-in stderr diagnostics | accepted |
| [0013](ADR-0013-channel-mirror.md) | Crash-safe mirror research design and R0 evidence | superseded by ADR-0014 |
| [0014](ADR-0014-lean-faithful-mirror.md) | Lean faithful channel mirror | accepted (feature replaced by ADR-0017) |
| [0015](ADR-0015-truthful-persistent-mirror-showcase.md) | Persistent private showcase and topology promotion gates | accepted (retention rules carry into clone) |
| [0016](ADR-0016-live-mirror-fidelity-corrections.md) | Service-message skip, reply reconstruction fallback, append-only TSV | accepted (fidelity rules carry into clone) |
| [0017](ADR-0017-clone-supersedes-mirror.md) | Clone rewrite supersedes mirror; JSON state, core-primitive reuse, complexity budgets | accepted |
| [0018](ADR-0018-clone-service-tail.md) | Clone tail verification accepts service-only rows | accepted |
| [0019](ADR-0019-clone-truthful-fallbacks.md) | Poll snapshots, named Story placeholders, reply continuity | accepted |
| [0020](ADR-0020-clone-channel-profile.md) | Init-time channel description and static avatar copy | accepted |
| [0021](ADR-0021-clone-attributed-sources.md) | Megagroup/dialog sources, hybrid attribution transport, reply-flatten reporting | accepted |
| [0022](ADR-0022-clone-forum-topics.md) | Forum destinations, lazy topic mapping, in-topic routing | accepted |
| [0023](ADR-0023-clone-channel-comments.md) | Comments via a linked discussion group; author-identity ladder; `init --replace` | accepted |
| [0024](ADR-0024-clone-source-roster.md) | Best-effort source-side participant roster snapshot during sync | accepted |
| [0025](ADR-0025-clone-preserve-reforward-header.md) | Per-batch `drop_author` keeps the native forward header on re-forwarded posts | accepted |
| [0026](ADR-0026-maintenance-mode.md) | Maintenance mode: fixes need a reproducing test; features need an ADR + scoped plan | accepted; rule 1 (posture) superseded by ADR-0071 |
| [0027](ADR-0027-ci-lint-typecheck.md) | CI enforces ruff lint/format and pyright basic over `src/` | accepted |
| [0028](ADR-0028-agent-correspondence-scope.md) | v1.1 agent correspondence: richer message JSON, pagination, full mutation set with random_id commits, discovery flags, doctor | accepted |
| [0029](ADR-0029-discovery-inbox-scope.md) | discovery & inbox quick-wins: resolve (+resolvePhone allowlist), contacts, media manifest, mark-unread/dialog pin, thread | accepted |
| [0030](ADR-0030-outgoing-formatting.md) | outgoing `--format {plain,md,html}` + additive `custom_emoji` harvest (MSG-001 partial) | accepted |
| [0031](ADR-0031-broadcast-subscriber-export.md) | full broadcast `export subscribers` via prefix-union; block `--limit > 200` | accepted |
| [0032](ADR-0032-data-plumbing-inbox-ergonomics.md) | data plumbing & inbox ergonomics: mutual-chats, archive/mute, incremental export, bulk media, RO batch | accepted |
| [0033](ADR-0033-agent-skills-workflow.md) | GitHub issue flow, triage labels, and single-context agent docs | accepted |
| [0034](ADR-0034-shared-read-operation-seam.md) | Shared typed read-operation seam for interactive CLI and batch | accepted |
| [0035](ADR-0035-cli-entry-split.md) | CLI entry split into parser/preflight/dispatch; budgets become ceilings | accepted |
| [0036](ADR-0036-clone-quote-replies.md) | Clone classifies quote replies by target reachability; understood-but-untransferable degrades and reports instead of wedging | accepted |
| [0037](ADR-0037-clone-quote-fallback-seam.md) | Split clone quote fallback rendering (`quote_fallback.py`) from the async resolver (`quotes.py`) | accepted |
| [0038](ADR-0038-versioned-releases-changelog.md) | Tagged patch release per feature; minor is an owner-declared milestone; `CHANGELOG.md` section + bump land with the feature | accepted; rule 3 mechanics amended by ADR-0058, tooled by ADR-0074 |
| [0039](ADR-0039-message-drafts.md) | `tg draft set\|show\|clear\|list`: set/clear under preview→commit with `old_text`, reads in the registry, own JSON object | accepted |
| [0040](ADR-0040-wacli-review-adoption-scope.md) | Adopt `store stats\|cleanup` (audit log + sessions untouchable) and offline-by-default `doctor --connect`; defer `--events` (→FEED-001) and `tg spec` (needs overturning ADR-0028) | accepted |
| [0041](ADR-0041-user-facing-guide-split.md) | Add task-shaped `docs/guide/` pages between SKILL.md and CONTRACT.md; contract wins on conflict; no docs site while the repo is private | accepted |
| [0042](ADR-0042-accounts-login.md) | `tg accounts login` requires explicit phone authorization, with native-dialog code/password collection and staged promotion; `show`/`remove` close the account lifecycle | accepted; QR start amended out by ADR-0088 |
| [0043](ADR-0043-process-hardening.md) | Shared atomic-write/lock-probe/TTL-classify seams with a fail-closed `write_text` ban; executable exit-code table; one-command gate; release runbook and reviewer subagent | accepted |
| [0044](ADR-0044-clone-title-prefix.md) | Tool-created clone peers (destination + discussion group) titled `[Clone] {name}` via one `attribution.destination_title` seam; state `source_title` stays clean; retro via idempotent init re-run | accepted |
| [0045](ADR-0045-clone-flood-containment.md) | Account-scoped clone FloodWait cooldown (restores the mirror-era guard); `clone init --no-comments` (posts-only clone, `comments: "disabled"`); preview flood hints (`peers_to_create`, `account_flood`) | accepted; decision 1 superseded by ADR-0072 (implemented) |
| [0046](ADR-0046-clone-destination-ergonomics.md) | Init mutes tool-created peers and files them into the "Clone" dialog folder; best-effort with honest markers, additive `ergonomics` JSON | accepted |
| [0047](ADR-0047-clone-parallel-chunk-transfer.md) | Reupload transfers file chunks with constant parallelism 4 (striped download + parallel part upload); sends and batch order stay sequential; measured basis: transfer = 92–98% of sync wall time | accepted; download leg superseded by ADR-0083 |
| [0048](ADR-0048-clone-poll-breakdown-vote.md) | Poll snapshots cast-and-retract a transient vote on anonymous open non-quiz polls to capture the per-option breakdown (own vote subtracted); other polls get an honest "breakdown unavailable" line | accepted |
| [0049](ADR-0049-clone-sync-progress.md) | `clone sync` emits plain single-line progress to stderr by default (batch counter, per-file transfer %, phase lines) via the shared media progress seam; non-contractual format, silenced by `2>/dev/null` | accepted |
| [0050](ADR-0050-clone-forward-attribution.md) | Reposted (forwarded) source posts get a truthful `Переслано от <label>` prefix on the reupload/snapshot paths; `needs_author` gains a `fwd_from` case; native re-forward of the proven original gated behind sender+date+content match | accepted; content key extended to entities and keyboard by ADR-0085 |
| [0051](ADR-0051-clone-windowed-phase-interleaving.md) | `clone sync` interleaves the posts and comments legs in 50-batch windows, bounded by the anchor scan; an unmapped cross-leg parent defers instead of flattening; amends ADR-0023's ordering clause only | accepted |
| [0052](ADR-0052-clone-short-flood-wait-and-media-reuse.md) | A `FloodWaitError` of ≤60 s is waited out in the foreground and retried once, under a 180 s per-run budget; reupload downloads persist in a per-clone media cache so a failed batch is not re-downloaded; amends ADR-0045's exit-on-flood clause only | accepted; decisions 1–5 superseded by ADR-0072 (implemented) |
| [0053](ADR-0053-json-error-envelope-on-stdout.md) | With `--json` the error envelope is written to stdout as the run's single JSON document and still mirrored to stderr; human/`--plain`/`batch`/exit codes unchanged | accepted |
| [0054](ADR-0054-clone-prefix-backfill.md) | `clone refresh` backfills body prefixes into already-copied posts under preview→commit, eligible only when the destination body is byte identical to the unprefixed source; poll snapshots, native re-forwards, and the discussion leg excluded | accepted |
| [0055](ADR-0055-clone-pinned-and-photo-fidelity.md) | `clone sync` pins the mapped source pin silently when the posts leg is exhausted (never unpins, never overrides an existing pin, reports status); photo downscaling is measured before it is fixed, and the striped path picks the largest `PhotoSize` explicitly | accepted |
| [0056](ADR-0056-project-presentation-and-community-health.md) | MIT license; `CONTRIBUTING.md` as the human short form of AGENTS.md; `SECURITY.md` with a private channel, redaction rules, and scope; `needs-triage` issue forms + PR template; README badges, contents, and a dark/light banner pair | accepted; items 4–5 posture wording amended by ADR-0071 |
| [0057](ADR-0057-lint-policy-expansion.md) | Ruff selection widens from `E4/E7/E9/F` to `E/W/F/I/UP/C4` (`UP040` ignored, `combine-as-imports`); `B`/`SIM`/`PTH`/`ARG`/`RUF` excluded with stated reasons; one-time layout-only cleanup, five architecture ceilings raised by the isort blank-line cost | accepted |
| [0058](ADR-0058-process-speed-revisions.md) | Integrator assigns version/CHANGELOG at merge; devlog is one file per session under `docs/devlog/`; ceilings get a +50 grace band (`--strict` for merge-time true-up); waves branch from the integration head; ADR-lite for XS/S | accepted; devlog cadence amended by ADR-0073 (per landed slice, not per session); rule 1 tooled by ADR-0074 (`scripts/prepare-release.py`) |
| [0059](ADR-0059-verification-infrastructure.md) | Hypothesis property tests pin the audit's defect classes (derandomized in the gate); PR-gated macOS CI leg runs the suite; pytest-xdist parallelizes gate and CI | accepted |
| [0060](ADR-0060-clone-state-sqlite-proposal.md) | Clone state moves to per-clone SQLite/WAL (measured: JSON path is quadratic, 168 MB written per 5k messages vs 0.1 MB); single reader, one-time JSON import + `.imported` backup, explicit export-state rollback; small files stay JSON | accepted |
| [0061](ADR-0061-comments-leg-entity-reuse.md) | Comments leg reuses the run's ResolveContext discussion entities across ADR-0051 windows instead of two GetChannels RPCs per window; verify_tail stays per-window | accepted |
| [0062](ADR-0062-job-session-role.md) | Named session roles (arbitrary names, `primary` reserved): `accounts login --role NAME` authorizes another device whose lock frees the primary during long jobs; global `--session-role` flag; no implicit fallback between roles | accepted |
| [0063](ADR-0063-tg-changes-design.md) | `tg changes --cursor` foreground feed, hybrid coverage: cursor-held channel subscriptions with full `read`-shape events, `channel_activity` signals elsewhere, per-scope loud gaps, deletion tombstones, `--wait` with 2 s settle, no state files | accepted |
| [0064](ADR-0064-forward-origin-from-message-chat.md) | Forward-origin `from_id` labels use `message.forward.get_chat()` / `get_sender()` when standalone `get_entity` refuses (issue #80) | accepted |
| [0065](ADR-0065-active-documentation-drift-gates.md) | Active-doc gate covers README discoverability/global flags/safety summaries, benchmark claims, MAP inventory, and devlog routing; shipped status closure becomes an explicit workflow duty | accepted |
| [0066](ADR-0066-voice-played-json-field.md) | Additive `voice_played` field exposes Telegram voice playback state without mutation | accepted |
| [0067](ADR-0067-pinned-runtime-diagnostics.md) | Supported session runtime boundary plus additive `doctor` runtime fingerprint | accepted |
| [0068](ADR-0068-local-archive-store.md) | Native `tg archive` SQLite+FTS5 store: private dialogs auto-scoped, append-only history, local transcription; telecrawl sidecar rejected | accepted; refresh scheduling amended by ADR-0087 |
| [0069](ADR-0069-archive-exploration-module.md) | Keep Phase 5 archive search/read/history queries in a read-only archive module | accepted |
| [0070](ADR-0070-archive-refresh-scheduling.md) | Compose bounded archive refreshes and notify once after recurring failures | superseded by ADR-0087 |
| [0071](ADR-0071-owner-gated-development.md) | Posture renamed to owner-gated development: same gate (owner request + ADR + scoped plan; fixes start from a reproducing test; agents never widen scope), without the retired "feature-complete / do not add features" claim | accepted; rule 1 mechanics amended by ADR-0073 (scoped plan only for campaigns) |
| [0072](ADR-0072-account-request-governor.md) | Account-wide request governor: cooldowns independent per Telegram request type (peer excluded on purpose), a self-verifying probe instead of a bypass flag, a persisted per-type pacing interval plus a windowed peer-breadth budget, the seam wrapping Telethon's `_call`, and the deadline demoted to a hang detector — supersedes ADR-0045 decision 1 and ADR-0052 decisions 1–5 | accepted; decision 3's defaults carry one live demonstration (#140) and both stated assumptions remain open; implemented across #145's phases; L3 authenticated fail-open amended by ADR-0089 |
| [0073](ADR-0073-risk-tiered-change-process.md) | Risk-tiered change process: a seven-trigger full lane (contract, safety, state, pacing, new dependency/module/abstraction, released behavior, and the enforcement mechanisms plus the agent contract itself) and a small-fix lane with no ADR, plan, index row, status edit, or release bookkeeping; documents ride with their code; plans only for campaigns of 3+ PRs; compatibility begins at a release tag; devlog per landed slice, ~15 lines — amends ADR-0071 rule 1 mechanics, ADR-0058 cadence, and ADR-0007's ADR trigger | accepted |
| [0074](ADR-0074-complexity-reset-and-release-preparation.md) | Complexity reset: a second related review finding on the same abstraction is a design checkpoint, not another patch, and unreleased code is not a sunk cost; `scripts/prepare-release.py` does the mechanical half of a release (version in both files, dated section, PR/ADR list, compare link) while the integrator writes the prose, refusing a version split across the two files rather than compounding it — extends ADR-0058 rule 1 | accepted (ADR-lite) |
| [0075](ADR-0075-transcribe-command.md) | `tg transcribe`: server-side voice transcription over `messages.transcribeAudio` with a race-free wait for the async `updateTranscribedAudio` result bounded by `--timeout`, Premium refusal mapped to the existing blocked class, and `voice_played`/auto-transcribe-in-`read` explicitly out of scope | accepted |
| [0076](ADR-0076-story-media-download.md) | `tg media download` accepts story links (`/s/<id>`, public and private), resolves via `stories.getStoriesByID`, selects an encoding from `document`/`alt_documents` by the `video_codec` attribute on opt-in `--codec`, and reuses the striped download machinery | accepted |
| [0077](ADR-0077-release-workflow-publishes-github-releases.md) | The `Release tag` workflow publishes the GitHub Releases page entry from the CHANGELOG section in the same run as the tag, idempotently (re-run repairs a publish failure) — the page stops being a manual follow-up | accepted |
| [0078](ADR-0078-codec-story-sources-only.md) | `tg media download` rejects `--codec` on non-story sources (message or bulk) with the existing `BLOCKED` exit 2 instead of silently ignoring the flag; the ADR-0076 encoding scope becomes enforced at the dispatch level | accepted |
| [0079](ADR-0079-transcribe-readonly-gate.md) | `messages.transcribeAudio` is classified as a mutation: `tg transcribe` is blocked by `--readonly` (exit 2) in preflight and writes an audit record with chat + message_id; the raw `tg api` write classification is unchanged | accepted |
| [0080](ADR-0080-release-notes-trim-blank-lines.md) | The release-tag workflow trims leading blank lines and whitespace-only sections from the published notes: a marker-only CHANGELOG section now fails the run red instead of publishing empty notes | accepted |
| [0081](ADR-0081-diagnostics-report-what-they-can-act-on.md) | `doctor` repairs the loose preview modes it checks, `clone status` counts unimportable state slots instead of listing empty rows and names the clone destination, and the clone state schema migrates forward in place | accepted |
| [0082](ADR-0082-clone-sync-answers-for-its-own-legs.md) | `clone sync`/`refresh` resolve a bare title from clone state instead of Telethon's entity cache, report an unopenable source as exit 4, and count progress per leg against that leg's own source | accepted |
| [0083](ADR-0083-a-flood-must-not-destroy-finished-work.md) | A flood must not throw away work that succeeded: the reupload download resumes across runs (serial + checkpointed), and clone commits use the retryable begin/finish preview handshake | accepted |
| [0084](ADR-0084-a-resume-must-identify-its-media.md) | A `media download` resume trusts a partial file only when the media's own id and byte size still match; a replaced file restarts the transfer instead of splicing two files together | accepted |
| [0085](ADR-0085-a-clone-does-not-invent-a-bots-keyboard.md) | Message `reply_markup` is outside clone fidelity: no transport but a native forward can carry a bot keyboard, so the clone reports the loss in `sync.markup_dropped` (exit 0) and never rebuilds the buttons; the ADR-0050 re-forward proof gains the keyboard as a content key | accepted |
| [0086](ADR-0086-clone-reports-permanent-degradation-before-run-exit.md) | `clone sync` reports each durable quote fallback and unsupported-message skip on stderr immediately, so a later flood cannot erase the only operator-visible evidence; existing JSON and exit semantics stay unchanged | accepted |
| [0087](ADR-0087-foreground-persisted-jobs.md) | `tg jobs` persists four typed checkpointed workloads in an account-scoped SQLite registry and runs them through independent foreground Telegram/local lanes with bounded-aging priority, cooperative cancellation, retry state, explicit role/safety gates, and launchd-owned recurrence; replaces `archive refresh` without a daemon | accepted; implementation campaign #146 |
| [0088](ADR-0088-phone-only-session-authorization.md) | `tg accounts login` starts only with explicit `--phone`; removes QR token/deep-link/wait behavior and `--qr-format` while retaining staged code/password continuation for primary and named-role sessions | accepted; owner request #193 |
| [0089](ADR-0089-degraded-governor-ledger-fails-closed.md) | Authenticated governed traffic refuses (`PolicyError`) when `governor.db` cannot be opened; `doctor` sets `ok: false` on `governor_degraded` | accepted; amends ADR-0072 L3; owner request #205 |
| [0090](ADR-0090-failed-flood-arm-fails-closed.md) | Failed FloodWait cooldown arm keeps a sticky process-local deadline and re-raises FloodWait; next same-type refuses with exit 5 | accepted; thermos T02 / #206 |
| [0091](ADR-0091-media-download-completeness.md) | `media download`'s serial loop requires `current == size` before publishing and fsyncs its part file before each checkpoint; `transfer.download_striped` raises and unlinks when its own downloaded-byte counter falls short of `size`, since the pre-allocated file's on-disk size cannot prove completeness — mirrors ADR-0083 decisions 1 and 3 onto the one download path they did not reach | accepted; thermos audit T03 |
| [0092](ADR-0092-api-write-auth-account-namespace-deny.md) | `tg api --write` wholesale-denies `auth.*`/`account.*` (mirrors ADR-0010's read-path exclusion), not just the four named `HARD_DENYLIST` methods | accepted; thermos audit T04 |
| [0093](ADR-0093-login-phone-continuation-sidecar.md) | Pending phone login keeps the raw phone only in an atomic `0600` sidecar required by Telegram sign-in; every attempt inventory and deletion path owns it | accepted; thermos T08 / PR #248 review |
| [0094](ADR-0094-store-cleanup-login-lock-uncertainty.md) | `store cleanup` probes a staged login's existing lock directly and treats every result except definitely free, including `flock` errors, as busy | accepted |
| [0095](ADR-0095-runtime-error-phone-redaction.md) | Runtime errors redact compact and formatted phone-shaped text before CLI emission or job persistence, including opt-in verbose tracebacks | accepted |
| [0096](ADR-0096-archive-sqlite-contention-policy.md) | Archive connections explicitly pin a 5-second SQLite busy timeout instead of inheriting the CPython driver default | accepted; thermos audit T24 |
| [0097](ADR-0097-archive-scope-cursor-atomicity.md) | Archive remove drops scope and channel subscription in one SQLite write transaction; sync cursor writers re-project onto current scope under the same lock | accepted; thermos T09/T23 |
| [0098](ADR-0098-session-stem-path-escape-rejection.md) | `load_config` rejects a `session` stem containing `/`, `\`, `..`, a null byte, a leading `-`, `@`, or empty; `session_path` adds an independent resolve-under-`sessions/` check as defense in depth (thermos T07) | accepted |
| [0099](ADR-0099-session-file-lock.md) | Canonical `session.session_file_lock` for non-blocking `.lock` acquisition; call sites keep ConfigError vs PolicyError via `busy_error` | accepted; thermos debt T26 |
| [0100](ADR-0100-alias-charset-validated-on-load.md) | `load_config` validates every `[accounts.<alias>]` key against the alias-grade charset (previously enforced only by `accounts login` for a brand-new alias) and rejects `@` in a `session` stem (the role-suffix separator); overlaps T07/ADR-0098, which independently excludes `@` for its own path-escape reason | accepted (ADR-lite) |
| [0101](ADR-0101-batch-jsonl-strict-bool-int-coercion.md) | `tg batch` bool/int JSONL fields accept only a real JSON `true`/`false`/integer (never `bool()`/`int()` truthiness or `int` coercion of a `bool`); a mistyped field is exit 2 `BLOCKED` before that op's Telegram fetch runs | accepted; thermos audit T15 |
| [0102](ADR-0102-expired-preview-never-sticky-pending.md) | `begin_commit` checks kind and TTL before any rename to `.pending`, and returns an already-`.pending` retry found expired back to `.json`; an expired preview always lands in the plain expired bucket `store cleanup` reaps by default (T13) | accepted |
| [0103](ADR-0103-clone-id-includes-source-peer-class.md) | `clone_id` hashes the source's peer class (user/chat/channel), not the bare numeric peer id, so a User/basic-group/Channel collision on one integer no longer shares a clone state slot; a pre-ADR-0103 slot migrates onto its class-aware id lazily on first resolve | accepted; thermos T05 |
| [0104](ADR-0104-clone-lookup-uses-peer-class-tokens.md) | Numeric clone filters match the canonical token for the recorded source peer class: raw user id, negative basic-group id, or `-100`-marked channel id; cross-class aliases are refused | accepted; thermos T18; depends on ADR-0103 |

Notes on supersessions:

- ADR-0009 supersedes ADR-0006 entirely (no TDLib in v1).
- ADR-0010 supersedes only ADR-0008's phase-2 read-classification rule;
  the rest of ADR-0008 (write gating, denylist, audit) remains in force.
- ADR-0014 supersedes ADR-0013. The mirror feature itself (ADR-0013…0016)
  was replaced wholesale by clone (ADR-0017); ADR-0015 destination
  retention and ADR-0016 fidelity rules carry forward into clone, which is
  why 0014–0016 stay "accepted" as rule sources while the mirror surface
  is gone.
- ADR-0072 supersedes ADR-0045 decision 1 (account-scoped cooldown storage,
  clone-only enforcement) and ADR-0052 decisions 1–5 (the
  `SHORT_WAIT`/`WAIT_BUDGET` foreground-retry mechanism). Both supersessions
  are effective: the account-scoped JSON record, the foreground retry,
  `WaitBudget`/`FloodGate` and the pre-flight account gate are deleted, and
  the governor's per-request-type ledger plus the governed `_call` seam are
  what actually runs. ADR-0045 decisions 2–3 (decisions 2 in force;
  decision 3's `account_flood` preview field removed) and ADR-0052
  decisions 6–7 (the reupload media cache) carry forward as noted in the
  ADRs themselves.
- ADR-0071 supersedes only ADR-0026's rule 1 (the "maintenance mode /
  feature-complete" posture wording). ADR-0026 rules 2–4 — scope routing to
  docs/ISSUES.md, the clone chronicle in docs/CLONE.md, and this index —
  remain in force, which is why 0026 stays a live rule source. ADR-0071 also
  amends the posture wording ADR-0056 items 4–5 prescribe for the README
  badge and the proposal form; those surfaces are otherwise untouched.
