# Research: prior telecrawl integration artifacts on this machine (issue #102)

Read-only filesystem audit of every prior attempt to integrate
[`openclaw/telecrawl`](https://github.com/openclaw/telecrawl) (a local Telegram
Desktop `tdata` archive + FTS5 search CLI) into this workspace, done to gate
the new local-archive wayfinder map (issue #100 and children #101-#109).

## Executive summary

- **What worked:** telecrawl (Homebrew-installed Go binary) reliably imports
  Telegram Desktop `tdata` into a local SQLite + FTS5 archive. It was wired up
  for **four separate Telegram accounts**, produced a working read-only
  operator-gated wrapper (`tools/agent-tooling/bin/telecrawl-archive`,
  `WRAPPER_VERSION = "telecrawl-archive-mvp-3"`) with manifest-based coverage
  claims, a hard 50-row result cap, checkpoint/resume patches, and rate
  limiting — and the resulting archive was good enough to power a full
  cross-account personal-relationship analysis
  (`outputs/analyses/2026-05-20-telecrawl-personal-dialogs`).
- **What broke — the "everything got downloaded" incident, confirmed:** on
  2026-06-16, a request to refresh coverage for **one channel** (`@dreamertim`)
  was executed as `telecrawl-archive --operator ... import-fast
  --dialogs-limit 0 --messages-limit 0` — the wrapper's own "unlimited" sentinel
  — and silently re-imported the **entire** `tdata` source: 1,312 chats,
  818,377 messages, spanning 2015-10-28 to 2026-06-16, across every private
  chat, group and channel on that account. The manifest for that run
  (`telecrawl-fast.db.manifest.json`) literally records
  `"dialogs_limit": 0, "messages_limit": 0, "full_import_requested": true`.
  There is no per-dialog allowlist or scope ceiling anywhere in the wrapper,
  telecrawl's own CLI, or the control-plane policy layer — `--operator` gates
  *who* can request a full import, not *how much* a full import can touch.
- **Why abandoned:** no explicit "telecrawl killed because X" record exists.
  The wrapper, its patches, and its test file were **never committed** to
  `tools/agent-tooling`'s git repo (all show as untracked `??` in `git
  status`), so they never became durable, reviewed project state. The last
  dated activity is the 2026-06-16 refresh; nothing after that. Structurally,
  telecrawl's home was `tools/telegram/control-plane` (an `archive_snapshot`
  evidence source wired into its doctor/gap-policy machinery) — that whole
  daemon-first control-plane stack was retired when tgcli's **ADR-0002**
  ("Stateless CLI core; no daemons") replaced it, citing reliability
  incidents and stating "the entire control-plane/drift class of tooling
  becomes unnecessary." telecrawl was retired as a rider on that decision, not
  as its own decision.
- **Where the local copy lives:** binary at `/opt/homebrew/bin/telecrawl`
  (Homebrew Cellar `telecrawl 0.1.0`, tap `openclaw/homebrew-tap`, formula
  currently offers `0.3.5` — 0.1.0 remains linked/installed). No
  `openclaw/telecrawl` source checkout exists anywhere on the machine; the
  only build artifact is the standalone `tools/agent-tooling/bin/telecrawl-fast`
  binary (11.5 MB Mach-O, presumably telecrawl rebuilt from source with the two
  local patches applied — provenance not independently verifiable read-only
  since no source tree remains). Runtime state under `~/.telecrawl/` (venv,
  10 account `.session` files under `~/.telecrawl/sessions/`). Archive data
  under `/Users/sereja/Projects/.artifacts/telecrawl/*.db` (four per-account
  DBs + manifests + dated backups).

---

## 1. `tools/agent-tooling` — the read-only wrapper

Path: `/Users/sereja/Projects/tools/agent-tooling`

- `bin/telecrawl-archive` (Python, 50,807 bytes, `WRAPPER_VERSION =
  "telecrawl-archive-mvp-3"`, last modified 2026-05-18 20:56).
- `bin/telecrawl-fast` (11,510,402-byte compiled binary, last modified
  2026-05-19 11:16) — a separately built telecrawl binary distinct from the
  Homebrew-installed `telecrawl`.
- `tests/test_telecrawl_archive.py` (8,837 bytes, 2026-05-18 15:41) — six
  passing-shaped unit tests against a fake `telecrawl` stub.
- `patches/telecrawl-telegram-message-95ef6f2b.patch` (12,484 bytes,
  2026-05-19 00:24) and `patches/telecrawl-tl-poll-and-cursor-resume.patch`
  (31,829 bytes, 2026-05-19 11:52) — diffs against telecrawl's own Go/Python
  internals (`internal/telegramdesktop/importer.go`,
  `internal/telegramdesktop/scripts/import_tdata.py`).
- **All four are untracked in git** — `git status --short` on
  `tools/agent-tooling` shows `?? bin/telecrawl-archive`, `?? bin/telecrawl-fast`,
  `?? patches/`, `?? tests/test_telecrawl_archive.py`, and `git log --oneline
  --all` for any of these paths returns nothing. They were built and used
  locally but never entered the repo's history.
- `README.md:195-225` documents the wrapper's contract: wraps
  `openclaw/telecrawl` as a "read-only Telegram Desktop archive entrypoint for
  agents"; default DB
  `/Users/sereja/Projects/.artifacts/telecrawl/telecrawl-fast.db`; supports
  `import-fast` through the patched local `./bin/telecrawl-fast`, "streams
  batches into SQLite, records checkpoints"; keeps multi-account archives
  separated by DB "because upstream message/chat keys can collide across
  accounts"; explicitly "does not send/reply and does not run `telecrawl
  backup`."

### Safety mechanisms present (and their limits)

`bin/telecrawl-archive`:

- `enforce_operator_gate()` (lines 53-78): blocks any `--db`/`--tdata` path
  override, and any `import-pilot`/`import-fast`/`manifest-init` call, unless
  `--operator` is passed. Read paths (`search`, `status`, `chats`, `messages`,
  `context`) work without `--operator` against the default DB only.
- `MAX_RESULT_LIMIT = 50` (line 21) and `enforce_limit()` (lines 81-92): hard
  caps any single `search`/`messages` call to 50 rows, regardless of operator
  status.
- `full_requested = dialogs_limit == 0 and messages_limit == 0` (lines 734,
  786): **`0` is the documented sentinel for "no limit."** Defaults are
  `--dialogs-limit 50 --messages-limit 100` (`imp`/`fast` parser defaults,
  lines 962-967), but any operator-mode call can pass `0 0` to request a full,
  unbounded import — and the code path only *labels* that afterward via
  `coverage_claim` (`full_verified_archive_snapshot` if it also passed
  integrity checks with no import gaps); it never refuses the request or asks
  for a scoped dialog list.
- There is **no per-dialog allowlist parameter anywhere** in this wrapper, in
  telecrawl's own CLI surface as invoked here, or in the control-plane policy
  layer (section 2). Scope control exists only as "pass a positive
  `--dialogs-limit`/`--messages-limit`," which is a volume cap, not an
  identity-based allowlist — it does not stop a "refresh this one channel"
  intent from being executed as "reimport everything."

`tests/test_telecrawl_archive.py` confirms the intended design in
`test_manifest_init_records_bounded_coverage_and_readiness` (asserts
`coverage_claim == "bounded_archive_snapshot"` and `full_import_verified ==
False` for a bounded 200/500 import) — i.e., the design assumed callers would
normally pass bounded values; the `0`/`0` full-import path was a rarer,
deliberate escape hatch that was actually exercised in production (see §5).

## 2. `tools/telegram/control-plane` — the old daemon-first stack

Path: `/Users/sereja/Projects/tools/telegram/control-plane`

- `src/telegram_control_plane/telecrawl_gap.py` — treats telecrawl as
  `"archive_snapshot"` evidence, `is_live: false` (from
  `policy/telecrawl.json`). Implements `import_gaps()` (reads an
  `import_errors` table and classifies rows into retryable vs terminal using
  `non_retryable_error_types`), `default_archive_status()` (manifest +
  gap-derived `coverage_claim`, escalating to
  `"partial_archive_snapshot_with_known_gaps"` when gaps exist), and
  `evaluate_archive_readiness()` (feeds `tg doctor`-style findings, e.g.
  `telecrawl_active_archives_incomplete`, `telecrawl_source_kind_unexpected`).
- `bin/telegram-telecrawl-status` — 4-line shell wrapper invoking
  `python3 -m telegram_control_plane telecrawl-status`.
- `policy/telecrawl.json` — non-retryable error list (`ChannelPrivateError`,
  `ChatAdminRequiredError`, `UserBannedInChannelError`,
  `UserNotParticipantError`, `ChannelInvalidError`, `InviteHashExpiredError`,
  `InviteHashInvalidError`); `known_gaps_are_blocking_for_archive_search:
  false` but `known_gaps_are_blocking_for_current_claims: true`; delegates
  `negative_results_claim` / `route_current_latest_today_send_reply_media_to`
  to a separate `policy/source-routing.json` (`"source_evidence_owner":
  "policy/source-routing.json"` — a refactor vs. the older
  `telegram-agent-control-plane` copy, see §3).
- Git history for these three files (`git log --oneline`) is just the repo's
  general commit stream — `b968de8 Improve Telegram control-plane operator
  gates (#11)`, `105244d Deepen Telegram control-plane architecture seams
  (#8)`, `4dad06f Sync agent kit milestones into telegram-plugin monorepo`,
  `0669c6a Release v1 Telegram agent control plane` — no commit message
  specifically calls out telecrawl deprecation or the download incident.
- `CONTEXT.md:36,52-53,60`, `MAP.md:57-70`, `PLAN.md:17`, `README.md:51,213,
  232-234,264` all describe telecrawl as one of three evidence routes
  (`live_mcp` / `telecrawl_archive` / mirror), classify it as archive-only
  evidence, and record `MAP.md:59-70` pointing at
  `/Users/sereja/Projects/.artifacts/telecrawl`, `/Users/sereja/.telecrawl`,
  and the two `tools/agent-tooling/bin/telecrawl-*` binaries from §1 — i.e.
  control-plane's own map explicitly acknowledges telecrawl artifacts live
  outside its own repo, in agent-tooling.
- No ADR or devlog in this repo announces control-plane's (or telecrawl's)
  retirement — that decision was made from the tgcli side (§4).

## 3. `telegram-agent-control-plane` — the published predecessor/fork

Path: `/Users/sereja/Projects/telegram-agent-control-plane`
(`git remote -v` → `https://github.com/speech115/telegram-plugin.git`)

This is the published GitHub plugin repo ("telegram-plugin"), sharing commit
`4dad06f Sync agent kit milestones into telegram-plugin monorepo` with
`tools/telegram/control-plane` — i.e. the two repos co-existed and diverged
from a shared point; `control-plane` (private working copy) continued past
that point with `#8`/`#11`, while `telegram-agent-control-plane` continued on
its own publishing track (`e5436c3 Add portable CI release gate and
fresh-install smoke`, etc.).

- `control-plane/policy/telecrawl.json` here is the **pre-refactor** version:
  it still carries `"negative_results_claim": "no matches in this archive
  coverage"` and `"route_current_latest_today_send_reply_media_to":
  "live_mcp"` inline, rather than delegating to `source-routing.json` as the
  newer copy in §2 does (`diff` confirms this is the only difference).
- `control-plane/bin/telegram-telecrawl-status` is byte-identical in shape to
  §2's.
- `docs/operator-workflows.md:86-88` ("Telecrawl Archive... Telecrawl-style
  archives are historical search aids. They can find candidate...") and
  `docs/fresh-install.md:57` mention it as one operator workflow among several
  (`mirror / telecrawl operator workflows`).
- `README.md:194` documents a `TELECRAWL_ARCHIVE_BIN` env var for pointing at
  a custom binary — consistent with §1's `TELECRAWL_ARCHIVE_TELECRAWL_BIN`
  override in the wrapper's test harness.
- No telecrawl-specific deprecation notice here either; this repo is simply
  the earlier/published snapshot of the same control-plane design in §2.

## 4. Why abandoned — tgcli's own record

`docs/decisions/ADR-0002-cli-first-stateless.md` (accepted 2026-07-06):

> The old stack is daemon-first: 4 MCP daemons on ports 8799-8802,
> LaunchAgents, auth tokens, a control-plane to detect drift, plugin-cache
> parity checks. Every reliability incident traced back to this layer... `tg`
> is a stateless process: connect -> do the task -> disconnect -> exit... The
> entire control-plane/drift class of tooling becomes unnecessary.

This is the operative "why abandoned" record. It does not name telecrawl
specifically — telecrawl was one evidence-source module *inside* the
control-plane it describes (per §2's `MAP.md`/`CONTEXT.md`), so it was
retired as a consequence of the daemon-first stack's replacement, not through
its own dedicated decision. No ADR, devlog, or commit message anywhere on
this machine states "telecrawl specifically caused an incident, therefore
removed" — the causal link has to be reconstructed from the manifest/output
evidence in §5, which this document now does explicitly.

tgcli's `docs/PROPOSALS.md:325-329` independently corroborates the general
posture that predates the new owner scenario: "The generic local mirror was
previously declined because Telegram has a server-side history and search
surface" — i.e. even before this specific incident evidence surfaced, tgcli's
maintainers already treated a full local mirror as a rejected default, only
reconsidering it now for a named, bounded owner scenario
(`docs/PROPOSALS.md:323-366`, `docs/devlog/2026-07-30-telecrawl-local-archive-proposal.md`,
`docs/devlog/2026-07-31-telecrawl-archive-wayfinder-map.md`).

## 5. What broke — reconstructing the "everything got downloaded" incident

### Direct evidence: the 2026-06-16 dreamertim refresh

`research/karpathy-kb/outputs/2026-06-16-telecrawl-dreamertim-refresh.md`
(status `done`) records the task as: "Обновить локальный `telecrawl` archive
snapshot для Telegram-канала `@dreamertim` / `Dreamer`" — refresh coverage
for **one named channel**. The executed command, per that devlog and the
resulting manifest, was:

```
telecrawl-archive --operator ... import-fast --dialogs-limit 0 --messages-limit 0
```

The manifest written by that run,
`/Users/sereja/Projects/.artifacts/telecrawl/telecrawl-fast.db.manifest.json`:

```json
"import": {
  "started_at": "2026-06-16T13:03:18.256010Z",
  "finished_at": "2026-06-16T13:03:49.555835Z",
  "dialogs_limit": 0,
  "messages_limit": 0,
  "full_import_requested": true,
  "full_import_verified": false,
  "last_source": "/Users/sereja/Library/Application Support/Telegram Desktop/tdata"
},
"counts": {
  "chats": 1312,
  "unread_chats": 118,
  "messages": 818377,
  "media_messages": 331200,
  "oldest_message": "2015-10-28T18:20:58Z",
  "newest_message": "2026-06-16T12:52:47Z"
}
```

`import_gaps.errors: 26` across 24 chats (14 `TypeNotFoundError`, 9-10
`TimeoutError`, 1-2 `ChannelPrivateError`) confirms this ran against the
*entire* account, not a filtered subset — a single-channel refresh has no
reason to touch 1,312 chats or produce `ChannelPrivateError`s on unrelated
private channels. The devlog itself only reports the target channel's count
(2,849 messages, unchanged) as the "answer," treating the full-archive
reimport as incidental machinery rather than the actual event — which is
exactly how "everything got downloaded" would be reported by someone who
only looked at the one channel they cared about.

### Corroborating evidence: multi-account, full-history personal analysis

`outputs/analyses/2026-05-20-telecrawl-personal-dialogs/README.md` — a month
earlier than the incident above — already shows telecrawl operating across
**four separate Telegram accounts** in full: roughly 411,178 messages total
across the four accounts combined, with per-account message counts ranging
from under 1,000 to over 280,000 and history depth back to 2014. The output
report goes on to analyze that full import at content level (per-contact
relationship and topic breakdowns) — details deliberately omitted from this
note since they name real people and personal topics; see the source path
above if that level of detail is ever needed, and treat it as sensitive.
This predates and independently confirms the pattern the June refresh made
undeniable: telecrawl's "targeted refresh" and "pilot" workflows were, in
practice, whole-account imports of every private conversation across every
connected account, immediately followed by content-level analysis of that
full personal history — not a scoped, per-dialog operation.

`~/.telecrawl/sessions/` holds 10 distinct `tdata-*.session` files plus a
`from-telegram-mac` folder, confirming telecrawl was pointed at many
account/tdata sources over time, each producing its own durable local copy.

### Root cause

`dialogs-limit 0` / `messages-limit 0` is the wrapper's own documented
"unlimited" sentinel (§1), accepted with no upper bound once `--operator` is
granted, and with **no dialog-identity scoping mechanism at all** anywhere in
the wrapper, telecrawl's own CLI as invoked, or the control-plane policy
layer. The failure mode was not a bug in the sense of incorrect code — the
wrapper did exactly what `0`/`0` is documented to mean — it was a **missing
scope primitive**: there was no way to ask telecrawl-archive "refresh only
`@dreamertim`" short of a full reimport, so operators reached for the full
reimport as the closest available tool, repeatedly, across multiple accounts.

## 6. Local telecrawl checkout / binary — exact locations

- `which telecrawl` → `/opt/homebrew/bin/telecrawl`.
- `brew list --formula` includes `telecrawl`; Cellar:
  `/opt/homebrew/Cellar/telecrawl/0.1.0/bin/telecrawl` (installed version
  **0.1.0**, formula metadata at
  `/opt/homebrew/Cellar/telecrawl/0.1.0/.brew/telecrawl.rb`).
- Tap formula `/opt/homebrew/Library/Taps/openclaw/homebrew-tap/Formula/telecrawl.rb`
  currently points at release **`v0.3.5`** (`darwin_arm64`/`darwin_amd64`
  tarballs from `github.com/openclaw/telecrawl/releases`) — i.e. upstream has
  moved on three-plus minor versions since the locally pinned/linked 0.1.0;
  no `brew upgrade` was ever run.
- **No source checkout** of `openclaw/telecrawl` exists anywhere searched
  (`~/Projects` minus `node_modules`, `~/go`, `/usr/local`, `~/bin`) — the
  two `.patch` files in `tools/agent-tooling/patches/` are the only surviving
  evidence of what was changed, as diffs against paths inside telecrawl's own
  module (`internal/telegramdesktop/importer.go`,
  `internal/telegramdesktop/scripts/import_tdata.py`).
- `tools/agent-tooling/bin/telecrawl-fast` (11,510,402 bytes, Mach-O
  executable) is presumably telecrawl rebuilt from source with both patches
  applied — this cannot be independently confirmed read-only since no build
  log or source tree remains; the `.patch` file paths and the wrapper's
  `import-fast` streaming-JSON contract (§7) are consistent with that binary
  being the patched build target.
- Runtime state: `~/.telecrawl/` — `venv/` (Python bridge env),
  `sessions/` (10 account `.session` files + `from-telegram-mac/`),
  `telecrawl.db` (90,112 bytes — small, likely an early/default pilot,
  distinct from the `.artifacts` archives).
- Archive data: `/Users/sereja/Projects/.artifacts/telecrawl/` —
  `telecrawl-fast.db` (818K messages, the incident DB), `telecrawl-76458740.db`
  (`@RecklessOU`), `telecrawl-1735244239.db` (`@TeamSyncSage`),
  `telecrawl-8006834784.db` (`@vermassov`), `telecrawl-accounts.db` (catalog),
  `telecrawl-full-resilient.db` (135,168 bytes, no manifest sidecar found —
  looks like an abandoned/empty alternate DB), plus `backups/` with two dated
  `.bak` pairs (`20260531-201514`, `20260616-130313.pre-dreamertim-refresh`).

## 7. Harvestable design — algorithm/approach detail

From `bin/telecrawl-archive` and the two patches:

- **Manifest-driven coverage claims** (`write_manifest()`,
  `bin/telecrawl-archive:718-769`): every import writes a sidecar
  `<db>.manifest.json` recording `coverage_claim` (one of
  `unknown_archive_snapshot` / `bounded_archive_snapshot` /
  `full_verified_archive_snapshot` / `partial_archive_snapshot_with_known_gaps`),
  `import.dialogs_limit`/`messages_limit`, `full_import_requested`/
  `full_import_verified`, `integrity_check` (`PRAGMA integrity_check`), and
  per-error-type gap counts. This is a genuinely reusable idea for a tgcli
  archive: never let a consumer confuse "I searched a snapshot" with "I
  searched everything" — but note it is a **label**, not an enforcement
  mechanism (see §5); a tgcli port must pair it with an actual scope cap.
- **Read-vs-mutate operator gate** (`enforce_operator_gate()`,
  `bin/telecrawl-archive:53-78`): default DB/tdata path usable read-only
  without elevation; any path override or import requires `--operator`.
  Directly portable pattern, matches tgcli's existing
  `preflight.py`/`--readonly` gate philosophy.
- **Hard result cap** (`MAX_RESULT_LIMIT = 50`, line 21): simple, effective,
  worth mirroring for any local-search command tgcli adds.
- **Checkpoint/resume format** (`patches/telecrawl-tl-poll-and-cursor-resume.patch`):
  streams one JSON event per line to stderr, `{"type": "checkpoint", "chat_id",
  "chat_name", "dialog_index", "total_dialogs", "status": "started"|"done",
  "cursor", "messages_imported"}` (patch lines ~355-382, 678, 735), and accepts
  a `--resume-chats-file` (patch lines ~189-195, 446, 623, 658, 680-682) whose
  JSON maps `chat_id -> {"offset_id": int, "messages_imported": int}` so a
  killed/interrupted import can resume per-chat from its last cursor instead
  of restarting. This is a clean, portable checkpoint design and lines up well
  with tgcli's existing `--events` NDJSON proposal
  (`docs/PROPOSALS.md:285-291`) and its ADR-0060 SQLite/WAL clone-state
  precedent — the shape is directly reusable for a tgcli-native archive
  backfill command.
- **Rate limiting**: `--batch-size` (default 1000) and `--wait-time` (default
  1.0 s) flags (`bin/telecrawl-archive:968-969`, patch lines 17-18, 151-155)
  — a fixed sleep between batches. Portable as a starting point, but
  tgcli's own `docs/PROPOSALS.md:436-437` already flags fixed concurrency as
  wrong for Telegram's rate limiting and calls for adaptive FloodWait backoff
  instead — any port of this should use that adaptive model, not the fixed
  `--wait-time` sleep telecrawl used.
- **Import-error taxonomy** (`policy/telecrawl.json` non-retryable list +
  `telecrawl_gap.py:import_gaps()`): splits import errors into terminal
  (`ChannelPrivateError`, `ChatAdminRequiredError`, `UserBannedInChannelError`,
  `UserNotParticipantError`, `ChannelInvalidError`, `InviteHashExpiredError`,
  `InviteHashInvalidError`) vs retryable (everything else, e.g. `TimeoutError`,
  `TypeNotFoundError`). Directly reusable taxonomy for tgcli's own proposed
  `failed` entry `kind: "temporary" | "permanent"` field
  (`docs/PROPOSALS.md:117-126`) — same underlying problem (don't re-fetch
  permanently-dead items, do retry transient ones), same shape of fix.
- **Backup-before-refresh discipline**: the dreamertim refresh created a dated
  `.bak` of both the DB and its manifest before running, matching a pattern
  worth keeping for any tgcli-native archive that supports destructive
  rebuild/refresh operations.
- **TL constructor fix** (`patches/telecrawl-telegram-message-95ef6f2b.patch:18`,
  `tlobjects.setdefault(0x95EF6F2B, types.Message)`): a narrow Telethon
  compatibility patch for an unrecognized message constructor that otherwise
  crashed import — informational only; not something tgcli needs unless it
  independently hits the same Telethon/TL gap.

## 8. What to avoid — precise, evidence-based

**Do not build a scope primitive whose only "unlimited" mode is a full,
unscoped reimport of the entire account.** The proven failure mode on this
machine is exactly that: `dialogs-limit 0 / messages-limit 0` was the *only*
way telecrawl-archive exposed to "make sure this one channel/dialog is
current," and every use of it (2026-05-18 initial pilot growing into
four-account 411K-message imports by 2026-05-20, then the 2026-06-16
`@dreamertim` refresh reimporting all 1,312 chats / 818,377 messages) pulled
in the full personal message history of every connected account instead of
the one dialog the operator actually cared about. There was no dialog-level
allowlist anywhere in the stack to prevent this, and the `--operator` gate
and `coverage_claim` manifest field — while good ideas — only gate *access*
and *label the result*; neither one caps *scope*. A tgcli-native archive must
make "refresh only these named dialogs" a first-class, independently
scoped operation, separate from (and cheaper/safer than) any "reimport
everything" primitive — which is exactly the correction tgcli's own
`docs/PROPOSALS.md:340-366` had already (independently) arrived at before
this audit ran: "The first candidate should be an explicit, foreground
import for selected dialogs, not a daemon or an automatic full-account
mirror" and "Private dialogs are the only default scope; groups/channels are
opt-in per explicit request" (`docs/devlog/2026-07-31-telecrawl-archive-wayfinder-map.md:15-17`).
This audit confirms that correction targets a real, previously-observed
failure on this exact machine, not a hypothetical one.

## Gaps / not found

- No git history anywhere explains telecrawl's abandonment in its own words —
  the causal chain in §4-§5 is reconstructed from dated devlogs, manifests,
  and analysis outputs, not from an explicit decision record. If a more
  direct "why we stopped" note exists, it was not found under any of the
  audited paths, `research/karpathy-kb/outputs`, or `research/karpathy-kb/raw`.
- `tools/agent-tooling/bin/telecrawl-fast`'s exact build provenance (which
  commit/tag of `openclaw/telecrawl` it was built from, whether both patches
  were applied cleanly) could not be confirmed read-only — no source
  checkout or build log survives.
- `telecrawl-full-resilient.db` (135,168 bytes) has no `.manifest.json`
  sidecar and was not otherwise referenced in any devlog found — its origin
  and purpose remain unclear from this audit.
- Per the task's read-only constraint, `telecrawl` itself was never executed
  (not even `--version`); all version/identity claims above come from
  Homebrew Cellar/tap metadata and file listings only.
