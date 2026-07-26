# FABLE5 Hardening Campaign — Progress Journal

Durable cross-session source of truth for the `claude/fable5-hardening-5zqywp`
campaign. Updated after every completed slice. Newest state on top of each
section.

Campaign goal: correctness, security, recoverability, stability, and
maintainability. No new features. Prefer deleting complexity, local fixes,
provable invariants.

## Current state

- Branch: `claude/fable5-hardening-5zqywp` (pushed to origin)
- Base: `main` @ `dca1eed` (1.2.15)
- HEAD: `2212652` — Phase 1 complete, all five handoff defects fixed
- Gate at HEAD (2026-07-26): ruff check passed; ruff format 147 files
  clean; architecture check passed; pyright 0 errors;
  **pytest 1163 passed, 9 skipped**; coverage OK (23 namespaces); docs
  gate 23 pages, 0 problems. (Baseline at `dca1eed` was 1132 passed.)

## Plan of record

1. **Phase 1 — DONE.** Five handoff defects fixed with red-first
   regression tests, independent adversarial review per diff, serial
   integration with a full gate per slice.
2. **Phase 2 — IN PROGRESS.** Full evidence-based audit (multi-lens
   workflow with adversarial verification of every finding).
3. **Phase 3** — fixes P0→P2 + simplification, including the
   owner-approved `commands/clone.py` split behind characterization tests.
4. **Phase 4** — release slice 1.2.16, whole-branch adversarial review,
   full gate, PR to main, CI. No merge without the owner.

Owner decisions already taken (2026-07-26): do not touch the fork;
reconstruct defects from specs. Deep audit budget approved. `clone.py`
split approved (after Phase 1, characterization tests first). No live
Telegram mutations.

## Confirmed findings (Phase 1 — all fixed)

All five handoff defects were independently confirmed at base by failing
regression tests before any fix:

1. **Permissions not enforced** (high): umask-dependent state dirs/files,
   `.session` 0644, doctor ignoring group bits and never checking the
   session file. 18 red tests at base.
2. **FloodWait did not stop sibling upload workers** (high): concurrent
   `upload_parts` workers kept issuing RPCs during a short wait and each
   charged the shared budget. 2 red tests at base.
3. **Pin crash-window latch** (medium): crash between UpdatePinnedMessage
   and the state save latched `pin_occupied` forever. 1 red test at base.
4. **Refresh broadcast-attribution on non-broadcast clones** (medium).
   Red CLI-seam test at base.
5. **Deleted album lead promoted a survivor** (medium). Red CLI-seam test
   at base.

## Confirmed findings (Phase 2 audit, 2026-07-26 @ `5b157c4`)

Method: 12 read-only lens agents → dedup → adversarial verification with
a refute-by-default stance (two independent skeptics per P1, one per
P2/P3) → completeness critic. 67 agents, ~3.5M tokens. **41 findings
confirmed, 0 refuted, 0 severity downgraded.** No P0 (no reachable
secret leak or unrecoverable data loss) was found. Two verifier agents
died on their output cap; the integrator verified those findings
(af-18, af-21) by hand — evidence quoted below.

**P1 — correctness (11).** af-01 poll retract lost to FloodWait/crash
leaves a real vote standing with no marker, and the pre-RPC audit row
claims a retract that never ran; af-02 hard-killed striped download
leaves a full-size sparse file the reupload cache accepts as complete;
af-03 `--timeout` escapes as a raw `TimeoutError` (traceback, empty
`--json` stdout, dead TIMEOUT code); af-04 `delete --commit` passes the
raw chat string to Telethon, so numeric-id deletes always crash at
commit; af-05 unknown chat in send prepare/commit exits 1 with a
traceback instead of exit 4; af-06 emitted dialog/peer ids are bare
Telethon ids, not the marked `-100…` ids CONTRACT documents, and are
inconsistent across commands; af-07 `clone status` crashes the whole
listing on a non-dict state payload; af-08 `CloneState.from_dict` skips
validation for `id_map`/`retry_not_before`; af-09 `load_config` crashes
on a malformed accounts table; af-10 `export --resume` crashes on a
truncated-UTF-8 tail instead of the documented exit 1; af-11
untranslated network/RPC failures print tracebacks with empty `--json`
stdout (CONTRACT §2).

**P2 — reliability/contract (16).** af-12 human/plain output emits
Telegram-controlled names without stripping control characters (§8);
af-13 `media download` interrupted before the first checkpoint
permanently crashes on retry; af-14 `login_state.promote` backup swap is
not crash-atomic; af-15 an ADR-0052 flood sleep cannot fit the 60s
default deadline; af-16 `quotes._peer_title` swallows FloodWaitError and
caches the miss for the run; af-17 deleted clone destination crashes
sync/init/refresh (only ValueError is caught); af-18 `tg api` numeric
peer aliases are parsed as phone numbers and never resolve (integrator
check: Telethon `utils.parse_phone('-1001234567890')` → `'1001234567890'`;
`api.py:210` passes the raw string, no `chatref.parse`); af-19 `clone
status` numeric filter compares a `-100` id against the stored raw id;
af-20 `export --resume` id-validity guard is untested; af-21 CI's
`uv sync --frozen` does not detect pyproject/uv.lock drift (integrator
check in a /tmp copy: pin changed to 1.43.0 → `uv sync --frozen` exit 0,
`uv lock --check` exit 1); af-22 `check-architecture.py` silently skips
renamed/missing state-writer modules; af-23 clone stderr progress
injects `\r`/escapes via a Telegram filename (§2); af-24/af-25 batch
silently drops or mis-accepts validated flags/enums; af-26
BrokenPipeError turns a successful run into exit 1 with a traceback;
af-27 the `--timeout` deadline is never armed around preflight, so
`tg batch` hangs forever on non-EOF stdin.

**P3 — maintainability/docs/ops (14).** af-28 store scan crashes on a
concurrently consumed preview; af-29 no parent-directory fsync after a
state rename; af-30 media-cache liveness guard is false during
upload-only phases; af-31 `get_me` RPC precedes cooldown enforcement;
af-32 cleanup deletes a live-but-expired staged login without probing
its lock; af-33 `t.me/c/` resolver ignores peer kind; af-34 `tg api`
strips sensitive keys while §6 promises the raw TL dict; af-35 login
`--continue` rejects a global flag; af-36 bulk `media download --plain`
breaks the TSV freeze; af-37 unreadable clone entry reports `""` where
CONTRACT promises null; af-38 guide omits the additive `pinned` field;
af-39 CSV formula-injection guard misses tab/CR/space-prefixed payloads;
af-40 the invocation journal falsifies exit codes on SIGINT; af-41 usage
errors emit no JSON document under `--json`.

**Completeness critic — areas the audit did not cover** (candidates for
a later pass, not findings): `tg api` allowlist side-effect semantics;
argparse abbreviation matching widening the CLI surface
(`allow_abbrev` is never disabled); clone state write amplification
(`state.save` rewrites the whole `id_map` per batch); wall-clock
dependence of every TTL/cooldown; `formatting.py` entity rendering;
SIGTERM/SIGHUP lifecycle; the platform/Python matrix (CI is
ubuntu-only while macOS is a first-class target); and Telethon
private-API dependence (`utils._photo_size_byte_count`) as a
pin-upgrade tripwire.

## Completed commits

- `e8d8eb9` — campaign progress journal.
- `5709eba` — D1: enforce private modes on state dirs, session, audit,
  journal files. `session.ensure_state_dir` (0700 create+repair, never
  above the state root), `restrict_file` (0600 fail-open), Telethon
  session tightened before connect on both normal and login paths,
  `login_state.promote`/`accounts import` mirror-fixes, doctor
  `_mode_ok` rejects group+other bits, additive `session_perms_ok`
  doctor key (.session + .bak), guide page updated. Includes the review
  fix for the `authclient.py` mirror-gap (probe path now repairs a
  loose pre-fix `sessions/` dir; red-tested against the pre-fix code).
  Gate: 1151 passed.
- `f1e78ce` — D2: `FloodGate` in `clone/flood.py` owned by `WaitBudget`;
  `_with_cooldown` parks siblings without RPCs or double budget charge;
  `download_striped` untouched; `note()` moved before `hold()` (review
  finding: stderr failure must not leave the gate held). clone.py
  ceiling 1450→1468 (debt repaid at split). Gate: 1154 passed.
- `9022c49` — D3: narrow pin crash recovery
  (`dest_pinned == decision.destination_id` → save, status `unchanged`,
  no RPC, no audit row); human-pin protection regression kept
  (`occupied` for a different cloned post). Gate: 1156 passed.
- `2212652` — D4+D5: refresh renderer mirrors sync's
  `source_kind == "broadcast"` branch (author_of with `me`/`source`
  threaded through preview and commit paths); album-lead proof via
  `bisect_left` over ordered mapped ids, fail-closed
  `album-lead-unknown` exclusion; hard case
  `test_album_survivor_excluded_when_lead_is_not_the_adjacent_id`
  covered. clone.py ceiling → 1474. Gate: 1163 passed.

## Checks performed

- Baseline `./scripts/gate.sh` at `dca1eed`: 1132 passed, 9 skipped, all
  gates green.
- Full gate re-run after **each** integrated slice (results above).
- Every fix red-tested at base by its implementer; D1/D2/D3/D45 red
  evidence independently reproduced by four separate reviewer agents.
- Reviews: D2/D3/D45 approve; D1 needs-work → its major finding
  (authclient mirror-gap) fixed and red-tested before integration.

## Open risks / carried debts (for Phase 2/3/4 triage)

Contract debt (must land in the 1.2.16 release slice, Phase 4):
- CONTRACT §5.1: additive doctor key `session_perms_ok`.
- CONTRACT §11: `album-lead-unknown` in the `excluded[].reason`
  vocabulary; `unchanged` also reachable via pin crash recovery (still
  two GetFull* RPCs on that run).
- `.gitignore`: add `invocations.jsonl` (handoff release-slice item).
- CHANGELOG 1.2.16 naming ADR-0045/0052 (gate), ADR-0055 (pin),
  ADR-0050/0054 (refresh); DEVLOG entry; version bump both files + lock.

Known residual gaps (documented, deliberately not fixed in Phase 1):
- Create-then-chmod TOCTOU window on new secret files (design per spec;
  a stricter fix would create at 0600 directly).
- Subdir mkdir sites not routed through `ensure_state_dir`
  (clone/state.py, clone/flood.py, clone/roster.py, resolve_phone.py,
  commands/media.py) — root is repaired every invocation; those dirs
  hold non-secrets. `.lock` files remain umask-mode (content-free).
- Pin recovery equality fails if the source pin changed during the crash
  window (needs a persisted `pin_pending` intent — state-schema work).
- D2: per-sibling redundant cooldown arming (idempotent, extra I/O);
  parked caller may get one extra free retry (bounded, disclosed).
- Refresh renderer duplicates sync's attribution branch (drift risk —
  factor when splitting clone.py).
- Reviewer FYI for audit: `topics.py::create_topic` may have its own
  crash window (create RPC before mapping save → duplicate topic).
- Release-tag drift (ADR-0038): upstream tags stop at `v1.2.9`;
  `v1.2.10`–`v1.2.15` are untagged (issue #72 covers `v1.2.10`). Tag
  pushes are denied from agent sessions (re-verified 2026-07-26) —
  owner-local work, listed below.

## Unfinished work

- Phase 3 fix waves over the 41 confirmed findings (P1 → P2 → P3),
  plus tracker issues #79/#81/#83 and the `clone.py` split.
- Phase 4 release slice 1.2.16 + PR.

## Tracker issues folded into Phase 3 (checked 2026-07-26)

- **#81** anonymous discussion comments attribute as `id unknown:` —
  in scope, mock-testable (live spot-check stays owner-side).
- **#79 + #83** document reupload drops still-image thumbs / sticker
  `PhotoPathSize` index crash — in scope; the "candidate patch" those
  issues mention never landed in the repo, so both are implemented from
  the spec as one slice (pick the thumb object, never a list index).
- **#80** missing forward origin, **#82** missing bot inline buttons —
  blocked: both need live dumps from the owner's account (and #82 needs
  a fidelity-contract decision first). Not in this campaign.
- **#72** tag `v1.2.10` — owner-local; agent sessions cannot push tags.

## Next concrete step

- Phase 3 wave 1: the P1 correctness findings, grouped by
  non-overlapping files, each red-tested first.

## Decisions needing the owner

- Release-tag policy: create the promised `v1.2.x` tags or amend ADR-0038
  so CHANGELOG is the single source of truth. Deferred to the Phase-4
  report.
- Merge of the campaign PR (never without the owner).
