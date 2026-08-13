> Wave E MERGED to main (PRs #272–#280), 2026-08-13.

# Thermos whole-repo audit backlog (2026-08-13)

Whole-module dual-pass audit (security + code quality) across nine
slices. This file is the **ticket-ready backlog**: each item below is one
GitHub issue to create. Cloud-agent tokens cannot open issues in this
repo; publish with:

```bash
uv run python scripts/publish-thermos-backlog.py          # dry-run
uv run python scripts/publish-thermos-backlog.py --apply  # create issues
uv run python scripts/publish-thermos-backlog.py --only T01 --only T04 --apply
```

Bodies live under `docs/thermos-audit-2026-08-13/tickets/`.

## Status

| Priority | Count | Meaning | Code status (2026-08-13 Wave E) |
|----------|------:|---------|----------------------------------|
| P0 | 4 | Safety / account-risk; fix first | landed on `main` |
| P1 | 9 | Data integrity / contract holes | landed on `main` |
| P2 | 12 | Ops hygiene / smaller bugs / spec drift | landed on `main` |
| Debt | 12 | Structural; often needs ADR / owner call | all landed on `main` (#272–#280) |

Owner request «Доделай все» closed Wave E (plan
`docs/plans/2026-08-13-thermos-debt-wave-e.md`). Bug tickets T01–T25 and
structural debt T26–T37 (plus the export `fsync_directory` smell) are in
code on `main`. GitHub issue create for the ticket bodies remains optional
owner-side work (`issues:write`).

## Dependency sketch

```
P0-gov-degraded ─┐
P0-gov-arm      ─┼─► (account safety net)
P0-media-complete┘
P0-api-write-deny

P1-clone-id ──► P1-clone-lookup (kind-aware matches)
P1-archive-remove ──► (pairs with scope-gate deletes in same slice)
Debt-media-resumable ──► absorbs P0-media-complete if done first
Debt-session-lock ──► unblocks cleaner login/doctor work
Debt-clone-send-split ──► unblocks further clone features at ceiling
Debt-jobs-store-split / Debt-archive-store-split ──► before next feature in those areas
```

## Index

### P0 — safety net

| ID | Title | Labels | Slice |
|----|-------|--------|-------|
| T01 | Governor: refuse or loudly fail when ledger is degraded | `bug`, `ready-for-agent` | Governor |
| T02 | Governor: fail closed when FloodWait arm cannot persist | `bug`, `ready-for-agent` | Governor |
| T03 | Media download: never publish incomplete / unsynced bytes | `bug`, `ready-for-agent` | Media |
| T04 | `tg api --write`: enforce ADR-0010 `auth.*` / `account.*` denylist | `bug`, `ready-for-agent` | Gates |

### P1 — integrity / contract

| ID | Title | Labels | Slice |
|----|-------|--------|-------|
| T05 | Clone id must include source peer kind | `bug`, `ready-for-agent` | Clone |
| T06 | Clone JSON→SQLite import must be crash-safe | `bug`, `ready-for-agent` | Clone |
| T07 | Reject path-escaping `session` stems in config | `bug`, `ready-for-agent` | Session |
| T08 | Keep full phone out of login attempt JSON (ADR-0088) | `bug`, `ready-for-agent` | Session |
| T09 | `archive remove` must drop channel from changes cursor | `bug`, `ready-for-agent` | Archive |
| T10 | Honor `--max-runtime` / stop through archive sync + media | `bug`, `ready-for-agent` | Archive |
| T11 | `store cleanup` must honor login lock without `.session` | `bug`, `ready-for-agent` | Gates |
| T12 | Delete reupload cache only after confirm + state save | `bug`, `ready-for-agent` | Clone |
| T13 | Expired `--commit` must not sticky-pending out of cleanup | `bug`, `ready-for-agent` | Safety |

### P2 — smaller bugs / hygiene

| ID | Title | Labels | Slice |
|----|-------|--------|-------|
| T14 | Bind or refuse forged `tg changes --cursor` channel sets | `bug`, `needs-triage` | Read |
| T15 | Batch JSONL: coerce bools/ints like CLI (reject `"false"`) | `bug`, `ready-for-agent` | Read |
| T16 | Journal flood fields only on flood exits | `bug`, `ready-for-agent` | CLI |
| T17 | Redact RUNTIME error envelopes (mask phones) | `bug`, `ready-for-agent` | CLI |
| T18 | Kind-aware `clone lookup.matches` for status filters | `bug`, `ready-for-agent` | Clone |
| T19 | `--replace` commit must not block on legacy clone cooldown | `bug`, `ready-for-agent` | Clone |
| T20 | Set `flood_sleep_threshold=0` on authclient | `bug`, `ready-for-agent` | Session |
| T21 | Make incremental export append crash-safe or document limit | `bug`, `needs-triage` | Gates |
| T22 | Align CONTRACT refresh commit text with ADR-0083 | `documentation`, `ready-for-agent` | Clone |
| T23 | Scope-gate channel deletes in archive sync | `bug`, `ready-for-agent` | Archive |
| T24 | Archive SQLite: set `busy_timeout` (and document concurrency) | `bug`, `ready-for-agent` | Archive |
| T25 | Validate alias charset on `load_config`; ban `@` in session stem | `bug`, `ready-for-agent` | Session |

### Debt — structure (owner-gated)

| ID | Title | Labels | Slice |
|----|-------|--------|-------|
| T26 | Unify session lock + path API (`session_file_lock`, `SessionSlot`) | `enhancement`, `needs-triage` | Session |
| T27 | Table-driven preview→commit; remove dead `consume_preview` | `enhancement`, `needs-triage` | Safety |
| T28 | Split `commands/clone.py` send execution out of the command module | `enhancement`, `needs-triage` | Clone |
| T29 | Split `jobs/store.py` before the next jobs feature | `enhancement`, `needs-triage` | Governor |
| T30 | Split `archive/store.py`; one peer-identity read seam | `enhancement`, `needs-triage` | Archive |
| T31 | Route media serial download through `transfer.download_resumable` | `enhancement`, `ready-for-agent` | Media |
| T32 | Extract archive parser/preflight; pull offline routing out of `cli.py` | `enhancement`, `needs-triage` | CLI |
| T33 | JobKind registry + shared lane loop / cooldown deferral | `enhancement`, `needs-triage` | Governor |
| T34 | Gate hardening: CI `--strict`, write-policy coverage, table-driven store scan | `enhancement`, `needs-triage` | Gates |
| T35 | Decompose `changes.py` poll/wait; add architecture ceiling | `enhancement`, `needs-triage` | Read |
| T36 | Atomic breadth check-and-touch in governor ledger | `enhancement`, `needs-triage` | Governor |
| T37 | Private archive-backfill job: cursored dialog enumeration | `enhancement`, `needs-triage` | Governor |

## What was deliberately not ticketed

- Doctor `--connect` governor bypass — intentional (CONTRACT §5.1 / ADR-0072).
- Draft read→save residual race — documented ADR-0039 limit.
- Audit create-then-chmod TOCTOU — already in `docs/PROPOSALS.md`.
- Symlink cleanup of live sessions — verified safe on Linux.
- Pure style / comment nits from quality passes.

## Source

Parallel thermos passes, 2026-08-13, nine slices: Safety & mutations,
Session & auth, Governor & jobs, Clone, Archive, Media & transfer,
Read path, CLI shell, Gates & contract surface.

## Landing (Wave E)

| ID | Branch / PR | ADR | Notes |
|----|-------------|-----|-------|
| T26 | merged | ADR-0105 / ADR-0099 | on `main` |
| T27 | merged | ADR-0111 | on `main` |
| T28 | #274 | ADR-0112 | on `main` |
| T29 | #275 | ADR-0113 | on `main` |
| T30 | #278 | ADR-0116 | on `main` |
| T31 | merged | — | on `main` |
| T32 | #277 | ADR-0115 | on `main` |
| T33 | #276 | ADR-0114 | on `main` |
| T34 | merged | ADR-0110 | on `main` |
| T35 | #273 | — | on `main` |
| T36 | #279 | ADR-0117 | on `main` |
| T37 | #280 | ADR-0118 | on `main` |
| smell | #272 | — | public `fsync_directory` on `main` |

GitHub issue create remains blocked for cloud tokens (`issues:write`); ticket
bodies stay under `docs/thermos-audit-2026-08-13/tickets/` for
`scripts/publish-thermos-backlog.py --apply` when an owner token has that
scope. Dry-run works; `--apply` fails without `issues:write`.

## Reviews (Wave E)

Independent Spec+Standards + thermos security/quality completed 2026-08-13.
See `docs/devlog/2026-08-13-wave-e-reviews.md` and
`docs/devlog/2026-08-13-wave-e-merge.md`. Confirmed blockers fixed before
merge. GitHub CI red = billing, not gate.
