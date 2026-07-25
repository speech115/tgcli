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
| [0007](ADR-0007-docs-discipline.md) | MAP + ADR + DEVLOG as mandatory agent workflow | accepted |
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
| [0026](ADR-0026-maintenance-mode.md) | Maintenance mode: fixes need a reproducing test; features need an ADR + scoped plan | accepted |
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
| [0038](ADR-0038-versioned-releases-changelog.md) | Tagged patch release per feature; minor is an owner-declared milestone; `CHANGELOG.md` section + bump land with the feature | accepted |
| [0039](ADR-0039-message-drafts.md) | `tg draft set\|show\|clear\|list`: set/clear under preview→commit with `old_text`, reads in the registry, own JSON object | accepted |
| [0040](ADR-0040-wacli-review-adoption-scope.md) | Adopt `store stats\|cleanup` (audit log + sessions untouchable) and offline-by-default `doctor --connect`; defer `--events` (→FEED-001) and `tg spec` (needs overturning ADR-0028) | accepted |
| [0041](ADR-0041-user-facing-guide-split.md) | Add task-shaped `docs/guide/` pages between SKILL.md and CONTRACT.md; contract wins on conflict; no docs site while the repo is private | accepted |
| [0042](ADR-0042-accounts-login.md) | `tg accounts login` QR-first + phone fallback, native-dialog secret channel, staged session promoted only on confirmation; `show`/`remove` close the account lifecycle | accepted |
| [0043](ADR-0043-process-hardening.md) | Shared atomic-write/lock-probe/TTL-classify seams with a fail-closed `write_text` ban; executable exit-code table; one-command gate; release runbook and reviewer subagent | accepted |
| [0044](ADR-0044-clone-title-prefix.md) | Tool-created clone peers (destination + discussion group) titled `[Clone] {name}` via one `attribution.destination_title` seam; state `source_title` stays clean; retro via idempotent init re-run | accepted |
| [0045](ADR-0045-clone-flood-containment.md) | Account-scoped clone FloodWait cooldown (restores the mirror-era guard); `clone init --no-comments` (posts-only clone, `comments: "disabled"`); preview flood hints (`peers_to_create`, `account_flood`) | accepted |
| [0046](ADR-0046-clone-destination-ergonomics.md) | Init mutes tool-created peers and files them into the "Clone" dialog folder; best-effort with honest markers, additive `ergonomics` JSON | accepted |
| [0047](ADR-0047-clone-parallel-chunk-transfer.md) | Reupload transfers file chunks with constant parallelism 4 (striped download + parallel part upload); sends and batch order stay sequential; measured basis: transfer = 92–98% of sync wall time | accepted |
| [0048](ADR-0048-clone-poll-breakdown-vote.md) | Poll snapshots cast-and-retract a transient vote on anonymous open non-quiz polls to capture the per-option breakdown (own vote subtracted); other polls get an honest "breakdown unavailable" line | accepted |
| [0049](ADR-0049-clone-sync-progress.md) | `clone sync` emits plain single-line progress to stderr by default (batch counter, per-file transfer %, phase lines) via the shared media progress seam; non-contractual format, silenced by `2>/dev/null` | accepted |
| [0050](ADR-0050-clone-forward-attribution.md) | Reposted (forwarded) source posts get a truthful `Переслано от <label>` prefix on the reupload/snapshot paths; `needs_author` gains a `fwd_from` case; native re-forward of the proven original gated behind sender+date+content match | accepted |
| [0051](ADR-0051-clone-windowed-phase-interleaving.md) | `clone sync` interleaves the posts and comments legs in 50-batch windows, bounded by the anchor scan; an unmapped cross-leg parent defers instead of flattening; amends ADR-0023's ordering clause only | accepted |
| [0052](ADR-0052-clone-short-flood-wait-and-media-reuse.md) | A `FloodWaitError` of ≤60 s is waited out in the foreground and retried once, under a 180 s per-run budget; reupload downloads persist in a per-clone media cache so a failed batch is not re-downloaded; amends ADR-0045's exit-on-flood clause only | accepted |
| [0053](ADR-0053-json-error-envelope-on-stdout.md) | With `--json` the error envelope is written to stdout as the run's single JSON document and still mirrored to stderr; human/`--plain`/`batch`/exit codes unchanged | accepted |
| [0055](ADR-0055-clone-pinned-and-photo-fidelity.md) | `clone sync` pins the mapped source pin silently when the posts leg is exhausted (never unpins, never overrides an existing pin, reports status); photo downscaling is measured before it is fixed, and the striped path picks the largest `PhotoSize` explicitly | accepted |

Notes on supersessions:

- ADR-0009 supersedes ADR-0006 entirely (no TDLib in v1).
- ADR-0010 supersedes only ADR-0008's phase-2 read-classification rule;
  the rest of ADR-0008 (write gating, denylist, audit) remains in force.
- ADR-0014 supersedes ADR-0013. The mirror feature itself (ADR-0013…0016)
  was replaced wholesale by clone (ADR-0017); ADR-0015 destination
  retention and ADR-0016 fidelity rules carry forward into clone, which is
  why 0014–0016 stay "accepted" as rule sources while the mirror surface
  is gone.
