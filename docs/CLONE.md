# tg clone — feature chronicle and current state

Extracted from PLAN.md's Non-Goals bullet (ADR-0026). PLAN.md keeps the
pointer; this file owns the clone story.

## Current capability

`tg clone` copies a Telegram source into a fresh, tool-owned destination.

- **Sources:** broadcast channels, non-forum megagroups, non-bot user
  dialogs, bot dialogs, live legacy basic groups (cloned like megagroups),
  and forum megagroups. Migrated/deactivated basic groups, secret chats,
  and cloning into pre-existing groups are rejected.
- **Destinations:** a private, tool-created and owned broadcast channel for
  most sources; a private, tool-created and owned **forum megagroup** for
  forum sources, with topics mapped 1:1 lazily during sync (ADR-0022).
- **Transport:** hybrid per source kind — broadcast posts use native
  forwarding, with `drop_author` decided per batch so posts that are
  themselves re-forwards keep their true origin header (ADR-0025);
  megagroup/dialog/protected or mapped-reply content is reuploaded with an
  identify-the-author attribution ladder (username, then profile mention,
  then bare id, then post signature — ADR-0021/ADR-0023).
- **Comments (ADR-0023):** a broadcast source with a readable linked
  discussion group gets its own tool-created, tool-linked megagroup,
  synced as a sequential second phase after posts, with its own cursor and
  comment-thread anchor remap onto the destination's auto-forward anchors.
  State and `status`/`init`/`sync` output carry a permanent `comments`
  field: `enabled` / `unavailable` / `none`. A stale posts-only clone can
  be superseded with `clone init --replace` (there is no comments backfill
  for an existing clone).
- **Roster (ADR-0024):** after both message phases, `sync` best-effort
  snapshots the source channel's and (when comments are enabled) the
  source discussion group's participants into a per-clone JSONL sidecar
  and a `participants` field on the `sync` response, with honest
  `collected`/`unavailable`/`deferred`/`none` markers. Never joins or
  writes to the source side.
- **Fidelity fallbacks (ADR-0019):** unsupported message kinds are skipped
  and reported, never fatal. Polls become truthful static result
  snapshots (native forwarding resets votes, so interactive fidelity is
  impossible). Stories become named placeholders. Unmappable replies
  flatten with an explicit `reply_flattened` report.

## History

- [ADR-0017](decisions/ADR-0017-clone-supersedes-mirror.md) — clone
  supersedes mirror: mirror reached live parity but its implementation
  grew disproportionate (own confirm/lock/cooldown machinery, SQLite
  migrations), so clone rebuilt the same live-proven behavior on shared
  core primitives (`safety.py`, `session.py`) with hard complexity
  budgets. Mirror ADRs 0013–0016 remain history; ADR-0015 retention and
  ADR-0016 fidelity rules carry forward.
- [ADR-0018](decisions/ADR-0018-clone-service-tail.md) — the first live
  gate hit Telegram's own service messages in the destination tail; clone
  accepts a service-only tail but still blocks on ordinary unexpected
  content.
- [ADR-0019](decisions/ADR-0019-clone-truthful-fallbacks.md) — a live
  canary proved native-forwarded polls reset votes and Story media is not
  recoverable; both became truthful static fallbacks, and reply
  flattening is reported explicitly.
- [ADR-0020](decisions/ADR-0020-clone-channel-profile.md) — init copies
  the source channel's non-empty description and static avatar before
  message sync begins.
- [ADR-0021](decisions/ADR-0021-clone-attributed-sources.md) — non-forum
  megagroup and private-dialog sources with hybrid native/reupload
  attribution and explicit reply-flatten reporting.
- [ADR-0022](decisions/ADR-0022-clone-forum-topics.md) — bot dialogs, live
  legacy basic groups, and forum megagroups become accepted sources; the
  destination invariant is kind-dependent (forum sources → forum-megagroup
  destinations). The read-only `mirror_probe.py` diagnostic was archived
  the same day (recoverable from git history).
- [ADR-0023](decisions/ADR-0023-clone-channel-comments.md) — round 3
  (spec: `superpowers/specs/2026-07-16-clone-comments-design.md`): comments
  via a second tool-created, tool-linked discussion megagroup synced as
  phase 2; global author-identity-ladder amendment to ADR-0021. The
  2026-07-17 amendment adds `clone init --replace` to supersede a stale
  posts-only slot (a bare re-`init` would reuse the old comment-less
  destination).
- [ADR-0024](decisions/ADR-0024-clone-source-roster.md) — best-effort
  source-side participant roster snapshot at the end of `sync`.
- [ADR-0025](decisions/ADR-0025-clone-preserve-reforward-header.md) — a
  live sample showed `drop_author=True` erasing the *original* forward
  header on posts that were themselves re-forwards; `drop_author` is now
  decided per batch.

## Live acceptance status

- **Core clone (Tasks 1–9)**: complete — JSON state, status,
  preview/commit init, text/media/album sync, mapped replies, protected
  reupload, canonical contract, controlled open/protected live acceptance,
  and removal of the mirror implementation.
- **Forum routing (ADR-0022)**: passed controlled live acceptance
  2026-07-16.
- **Comments (ADR-0023)**: full end-to-end gate (real source channel +
  discussion group, comment threads, idempotent rerun) **passed
  2026-07-17** against account `main`; four live-only bugs were found and
  fixed — see the ADR's "Live findings" section and
  `superpowers/plans/2026-07-17-clone-comments.md`.
- **Roster + newline attribution (ADR-0024, round 3.5)**: passed live
  2026-07-17 on a real 677-post channel with an active comment section —
  196 source-side participants collected into the sidecar (DEVLOG entry
  "live acceptance of roster + newline attribution").
- **Reforward header (ADR-0025)**: confirmed live on real re-forwarded
  posts per the ADR's own evidence.

## Deferred work

See [ISSUES.md](ISSUES.md) for full detail.

- **CLONE-001 — Poll cloning**: partially completed by ADR-0019. Open
  single/multiple-choice snapshots are covered by mocked tests and live
  evidence from two real polls; closed-poll and quiz fixtures remain open
  follow-up. Does not block clone.
- **CLONE-002 — Forum topics and legacy groups**: closed by ADR-0022.
  Still out of scope: cloning into pre-existing groups, secret chats,
  topic edit/close propagation.
