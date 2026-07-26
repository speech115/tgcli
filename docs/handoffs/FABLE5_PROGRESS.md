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

- Phase 2 audit: launching next.
- Phases 3–4: pending audit results.

## Next concrete step

- Run the Phase-2 multi-lens audit workflow over HEAD `2212652`;
  adversarially verify findings; record them here before fixing anything.

## Decisions needing the owner

- Release-tag policy: create the promised `v1.2.x` tags or amend ADR-0038
  so CHANGELOG is the single source of truth. Deferred to the Phase-4
  report.
- Merge of the campaign PR (never without the owner).
