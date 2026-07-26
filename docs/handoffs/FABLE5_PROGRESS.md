# FABLE5 Hardening Campaign — Progress Journal

Durable cross-session source of truth for the `claude/fable5-hardening-5zqywp`
campaign. Updated after every completed slice. Newest state on top of each
section.

Campaign goal: correctness, security, recoverability, stability, and
maintainability. No new features. Prefer deleting complexity, local fixes,
provable invariants.

## Current state

- Branch: `claude/fable5-hardening-5zqywp` (pushed to origin)
- Base: `main` @ `a07a021` (merged in; campaign started from `dca1eed`)
- PR: [#85](https://github.com/speech115/tgcli/pull/85) — open, awaiting
  the owner-authorised merge
- Phases 1-4 are complete. All four phases of the brief ran: baseline and
  the five handoff defects, the evidence-based audit, the prioritised
  fixes, and the adversarial whole-diff review with its fixes.
- Gate at HEAD (2026-07-26): ruff check passed; ruff format clean;
  architecture check passed; pyright 0 errors;
  **pytest 1419 passed, 9 skipped**; coverage OK; docs gate 0 problems.
  (Baseline at `dca1eed` was 1132 passed.)

## Phase 4 outcome — what is left for the owner

The engineering work is finished and reviewed. Three things remain that an
agent session structurally cannot do:

1. **Merge PR #85.** Only `squash` is available: the repository bans merge
   commits (`405 Merge commits are not allowed`) and the branch cannot be
   rebased because it contains a merge commit (`405 This branch can't be
   rebased`). This departs from the literal `gh pr merge N --merge` in
   `docs/agents/release.md`; that page should be amended to match the
   repository rules, or the rules relaxed.
2. **Tag the release.** `v1.2.16` must land on the merge commit per
   ADR-0038. Agent sessions cannot push `refs/tags/*`. The same pass should
   settle the `v1.2.10`-`v1.2.15` gap: either create the missing tags or
   amend ADR-0038 so CHANGELOG becomes the single source of truth.
3. **Live-dump issues.** #80 and #82 stay open — both need dumps from a
   real account, and live Telegram access was never granted this campaign
   (correctly: the brief forbids unauthorised mutations).

## Session-limit interruption (2026-07-26) — RESOLVED

The account hit its session limit mid-flight. Exact state:

- **Wave 3 produced nothing** the first time — all five fix agents died
  before committing. Re-run from scratch after the limit reset; all five
  landed (see below).
- **Wave 2 produced six commits but ZERO reviews** — every reviewer agent
  died. All six are now integrated on the branch (each with its own full
  gate) and the worktrees are gone:

  | group | worktree commit | landed as |
  |---|---|---|
  | w2-store-state | `a394a26` | `1565192` |
  | w2-untrusted-io | `474e19a` | `e50c33e` |
  | w2-clone-helpers | `6977d5b` | `9574844` |
  | w2-batch-media | `4825165` | `2a60d0a` |
  | w2-clone-runtime | `4e17e23` | `fab3548` |
  | w2-lifecycle | `f26d73a` | `a6e049f` |

  Every one was branched from `main` (`dca1eed`), not the campaign head, so
  each cherry-pick needed a ceiling reconciliation by the integrator.
  Landing `f26d73a` also needed a real merge: it had re-invented
  `DeadlineExceeded`/`TIMEOUT` inside `cli.py` because its base predated
  wave 1's `errors.CommandTimeoutError`. Resolution: the `errors.py` class
  survives, the whole-body `_armed` deadline and the `USAGE` envelope came
  across, and every wave-1 arm (`BrokenPipeError`, `_tolerate_hangup`,
  RUNTIME) was preserved.

- **All six were reviewed afterwards** by six independent reviewers
  (`reviewwave2.js`): one approve, five needs-work. Every blocker and major
  was fixed by the integrator with a red test first — see "Review fixes"
  below. The review debt is closed.

## Static-analysis and coverage measurement (2026-07-26)

Taken on a clean `/tmp` copy of the branch so nothing in the repo changed.
This answers the external review's "the gate is softer than it looks":

- **Line coverage is 94%** (5930 statements, 353 uncovered) — the gate never
  measured this before (`check-coverage.py` is a Telethon-namespace matrix,
  not a code-coverage tool). Thinnest modules: `commands/batch.py` 81%,
  `commands/api.py` 87%, `commands/media.py` 88%, `clone/replies.py` and
  `clone/comments.py` 89%, `commands/doctor.py` 89%. Coverage is high enough
  that it is not the reason defects survived — the audit's own finding
  classes (crash windows, concurrency, malformed input, clock skew) are
  invisible to line coverage by construction.
- **ruff rule families**, violation counts as measured: `B` 7, `C4` 5,
  `RET` 6, `UP` 21, `SIM` 25, `PTH` 37, `TRY` 360, `ARG` 840. Only `B`
  (bugbear) is worth adopting — three of its seven hits are `B023`, closures
  capturing a loop variable, which are latent by construction. Recorded in
  PROPOSALS; the adoption slice waits until wave 3 releases `transfer.py`.
- **pyright `strict` = 4956 errors**, nearly all `reportUnknown*` from
  Telethon's untyped surface. Not worth adopting; recorded in PROPOSALS so
  it is not re-litigated.

## Plan of record

1. **Phase 1 — DONE.** Five handoff defects fixed with red-first
   regression tests, independent adversarial review per diff, serial
   integration with a full gate per slice.
2. **Phase 2 — DONE.** 41 findings confirmed, 0 refuted; a follow-up gap
   audit over the completeness critic's uncovered areas confirmed 23 more
   and refuted 2.
3. **Phase 3 — fix waves DONE.** Waves 1-4 (17 slices) integrated, every
   one reviewed, every blocker/major closed with a red test. Tracker
   issues #79/#81/#83 fixed. Remaining: the owner-approved
   `commands/clone.py` split behind characterization tests.
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

## Confirmed findings (Phase 2b gap audit, 2026-07-26 @ `fbdf0ae`)

The critic's uncovered areas got their own 8-lens pass with the same
refute-by-default verification. 42 agents, ~2.2M tokens: **23 confirmed,
2 refuted** (`bf-09` int64 emoji-id crash, `bf-17` post-promote crash
guard — the skeptics disproved both). Ids are `bf-NN`.

The most serious defect of the whole campaign came from here:

- **bf-01 (P1)** — `--format html` silently TRUNCATES the message at
  unterminated markup, and the preview shows the untruncated text. The
  integrator reproduced it directly: `render("if a<b then c", "html")`
  returns `("if a", None)`; `render("x<y", "html")` returns `("x", None)`.
  Exit 0, empty stderr, and the audit record stores only the preview id —
  so an operator approves one message and Telegram publishes another, on
  `send`, `edit`, and `draft set` alike. A second loss mode is worse-behaved:
  `render("List<int> is generic", "html")` returns `("List is generic",
  None)` because `<int>` parses as a real start tag and is dropped, leaving
  `rawdata` empty. **The audit's own proposed fix was wrong** and the
  verifier caught it: `parser.close()` flushes `rawdata` (measured: it is
  `"<b then c"` before `close()` and `""` after), so the guard must read
  `rawdata` before closing, and a rawdata-only guard misses the unknown-tag
  mode entirely.
- **bf-02 (P1)** — an astral HTML character reference (`&#128512;`)
  desynchronizes every following UTF-16 entity offset, so bold/link/spoiler
  ranges land on the wrong characters in the published message.
- **bf-07 (P1)** — a positional value starting with `-h` (`tg send @user
  -hi`) is parsed as `-h` + `i`: help goes to **stdout** and the process
  exits 0 without sending anything.
- **bf-13 (P2, data-loss)** — `allow_abbrev` is never disabled, so the
  destructive gates are satisfiable by prefixes: `tg store cleanup --c`
  really deleted a preview in the auditor's executed reproduction.
- **bf-03 (P1, security)** — two aliases differing only in case resolve to
  one session file on case-insensitive macOS: two Telegram accounts share
  one authorization file.
- **bf-04 / bf-06 / bf-14 / bf-24 / bf-25 (P1–P3)** — the wall-clock family:
  a forward-skewed FloodWait arm bricks every clone command with no way out
  but hand-editing state; a host clock ahead of Telegram turns QR login into
  an `exportLoginToken` + desktop-open storm; the phone cooldown wedges
  after a backward step; a naive `expires_at` crashes `store`; a forward
  step destroys an in-flight login attempt on a read path.
- **bf-05 / bf-15 (P1–P2)** — `os.replace` in media download is not
  cross-filesystem safe (EXDEV), and `--parallel` writes no resume state, so
  a killed parallel transfer wedges that message.
- **bf-10 / bf-12 / bf-20 / bf-21 / bf-22 (P2–P3)** — `tg api`: an
  allowlisted read method is uninvokable, the write audit names only the
  method and never the target, the confirm gate misses irreversible
  `migrateChat` / `convertToGigagroup`, `--params` validity is checked after
  the session opens, and the allowlist tests never invoke anything.
- **bf-16 / bf-18 / bf-19 / bf-23 / bf-26 (P2–P3)** — the 0600 session
  tighten rests on undocumented eager SQLite creation with both guard tests
  faking the constructor; striped-download reassembly is never validated;
  `state.save` rewrites the whole file per message (quadratic I/O over a
  clone); usage errors share exit 1 with retryable failures; SIGTERM leaves
  no journal row.

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

### Phase 3 wave 1 — P1 correctness (integrated 2026-07-26)

Five agents, non-overlapping file scopes, each red-tested first and reviewed
independently. Reviews returned four needs-work verdicts; every blocker and
major was fixed by the integrator **before** the slice landed:

- `33e96d7` — af-03/af-11/af-26: `--timeout` expiry becomes a TIMEOUT
  envelope, untranslated failures become one RUNTIME envelope (traceback
  only under `-v`), a hung-up stdout pipe leaves quietly, and emit-phase
  failures are journaled. **Review fix:** `emit_error` now flushes and the
  error arms record their codes before writing, so a pipe closed mid-envelope
  can no longer blank the journal (red-tested: exit 0/journal `exit_code: 1`
  before, exit 4/`NOT_FOUND` after). Gate: 1173 passed.
- `ac658c9` — af-04/af-05: `send` preview/commit and `delete --commit` map an
  unresolvable chat to NOT_FOUND (exit 4). **Review fix:** `commit_edit`
  shared the same hole through Telethon's internal resolve inside
  `edit_message` — now mapped too, with a fake that rejects raw strings.
  Gate: 1179 passed.
- `28648fd` — af-07/af-08/af-09/af-10: non-dict clone state, unvalidated
  `id_map`/`retry_not_before`, malformed `accounts` tables, and a
  truncated-UTF-8 export tail all fail closed with documented errors.
  **Mirror-fix:** `media.py::_resume_offset` shared the bug class and was
  hardened in the same slice. Gate: 1224 passed.
- `b12a4fb` — af-01/af-02: a flood-blocked poll retract is now disclosed
  (stderr note + `retract_failed` marker) instead of vanishing into a generic
  exit 5, and the reupload cache proves completeness with a `.done` marker
  next to the file. **Review blocker fix:** staging into `.part` alone still
  let a *pre-existing* full-size sparse file (left by a pre-fix binary) be
  uploaded as real media; size alone no longer licenses reuse. Gate: 1229
  passed.
- `5db16b4` — af-18/af-19/af-21/af-22: `clone status` accepts both id forms,
  `tg api` numeric peer aliases resolve through `chatref` instead of being
  parsed as phone numbers, `uv lock --check` is now a CI **and** gate step,
  and a missing state-writer module fails the architecture check loudly.
  **Review fix:** the numeric branch gate is back to `isdigit()` — `int()`
  also accepts `+123`/`1_000`/padded forms and would have stolen titles from
  the substring path. Gate: 1240 passed.

Integration note: all five worktrees were branched from `main` (`dca1eed`),
not from the campaign head, so every ceiling in `scripts/check-architecture.py`
had to be reconciled by the integrator — exactly the conflict the new
AGENTS.md "shared files belong to the integrator" rule (`888d769`) describes.

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

1. **Review `1565192..a6e049f`** — six integrated but unreviewed slices.
   Nothing else should ship before this.
2. **Re-run wave 3 from scratch** (`scratchpad/wave3.js`, base = current
   head): `bf-01/02/08` formatting, `bf-03/16` config+session,
   `bf-10/12/20` api, `bf-04/14/06` clock skew, `bf-18` transfer proof.
   `bf-01` is the campaign's worst defect and is still unfixed.
3. **Wave 4** for the rest of the `bf-` set (`bf-05`, `bf-07`, `bf-13`,
   `bf-15`, `bf-19`, `bf-21`, `bf-23`, `bf-24`, `bf-25`, `bf-26`).
4. Then the `clone.py` split behind characterization tests, then the
   1.2.16 release slice and the PR.

## Review fixes landed by the integrator

Reviews returned needs-work on 8 of 11 reviewed slices. Every blocker and
major was closed on the branch, each with a failing test first:

- `9d43120` — **blocker**: the af-17 destination fix never reached the
  discussion-group destination (`clone/comments.py`) or `discussion.adopt`,
  both of which CONTRACT §11 promises as exit 2. All recorded-peer lookups
  now share one `PEER_UNAVAILABLE` tuple instead of three hand-copied ones.
- `e33f56c` — `clone/state.py::save()` and `clone/roster.py::_write()` were
  hand-rolled copies of `atomic.replace_text`, so they missed the parent
  directory fsync it had just gained; both now call it. Per-clone
  `retry_not_before` gained the same skew clamp its account-scoped sibling
  got.
- `c3f1345` — FloodWait was swallowed as "unknown author" when rendering a
  story and as "muted: false" during `clone init`; both now surface exit 5.
- `b07f674` (and its slice) — naive `expires_at` values crashed
  `load_attempt`, `consume_preview`, and `begin_commit` with a
  naive-vs-aware `TypeError`; all three now treat the record as unusable.
- Colliding aliases were rejected on read but still written by
  `accounts import`, producing a config no command could load; and the
  raw-write audit recorded `channel` but never `user_id`, so an
  `editAdmin` row named the room and not the person.

## Audit findings deliberately NOT fixed in 1.2.16

- **af-14** — `login_state.promote()` is not crash-atomic: the `.bak` swap
  and the staged move are two renames, so a crash between them leaves a
  backup and no live session. Manual recovery exists (rename the `.bak`
  back); a real fix needs a documented recovery path, not a wider rename.
  Disclosed in the CHANGELOG under "Known and not fixed".
- **bf-19** — `clone/state.py::save()` rewrites the whole state file per
  message, so disk I/O is quadratic over a clone. This is a performance and
  storage-shape question, routed to the measured backend work in
  `docs/PROPOSALS.md` (baseline first, SQLite prototype second) rather than
  patched blind.
- **bf-23** — usage errors share exit 1 with runtime failures. The envelope
  now distinguishes them (`USAGE` vs `RUNTIME`); changing the exit code
  itself would be a contract break needing an ADR.

## Wave assignments

- **Wave 2** (running): af-27/40/15/35/41 lifecycle; af-28/30/32/29 store
  and atomic; af-12/23/39/20 untrusted IO; af-24/25/13/33/36/06 batch and
  media; af-17/31/37/38 + issues #79/#83 clone runtime; af-16 + issue #81
  clone helpers.
- **Wave 3** (running): bf-01/02/08 formatting; bf-03/16 config and
  session; bf-10/12/20 `tg api`; bf-04/14/06 clock skew; bf-18 striped
  transfer proof.
- **Wave 4** (queued): everything above blocked on a file another wave
  owns, plus anything the reviews send back.

## Contract debt accumulated by Phase 3 wave 1 (for the 1.2.16 slice)

- §4: `TIMEOUT` as an exit-1 error code, reachable from every command.
- §4: a stdout pipe closed by the reader exits 0 (journal records
  `BROKEN_PIPE`); flip to 141 is a one-line change if the owner prefers the
  unix convention.
- §2: every failure — including untranslated ones — yields exactly one
  envelope on stdout under `--json` plus the stderr mirror.
- §11: the poll `retract_failed` marker is now also reachable via FloodWait;
  the reupload cache proves completeness with a `.done` sibling marker.
- §11: `clone status` accepts both the raw and the `-100`-marked source id.
- ADR: decide whether ADR-0053 gets an addendum or a new ADR covers "every
  failure path is an envelope"; ADR-0048 needs the flood-blocked retract
  path recorded.

## Decisions needing the owner

- Release-tag policy: create the promised `v1.2.x` tags or amend ADR-0038
  so CHANGELOG is the single source of truth. See "Phase 4 outcome" above.
- Merge of the campaign PR (never without the owner).
- Branch protection: the repository ruleset requires a `test` status check
  but treats a superseded, cancelled run of that check as a blocking
  failure. A concurrency-cancelled duplicate on the same SHA is therefore
  enough to make a green branch unmergeable. Worth pinning the rule to the
  latest run per check name, or dropping the workflow's concurrency
  cancellation.
