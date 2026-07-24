# DEVLOG

Append-only session log. Newest entry on top. Every agent session that
touches this repo adds one entry (AGENTS.md rule).

Sessions up to and including the 1.0.0 release live in
[DEVLOG-v1.md](DEVLOG-v1.md) and are closed — read them only when chasing
history, never to learn the current state.

Template:

```markdown
## YYYY-MM-DD — <short title> (<agent/model>)
**Did:** what actually changed (files, commands, results)
**Decided:** decisions made + link to ADR if architectural
**Learned:** surprises, gotchas, dead ends worth remembering
**Next:** the single most useful next step
```

## 2026-07-24 — ADR-0042 independent-review fixes (Composer)
**Did:** closed every finding from the stacked-PR review on tip
`cursor/accounts-login-release-371f`. Critical: `_classify_login` mtime
fallback + atomic attempt writes (`login_state._write_attempt`) with
regression tests. Major: QR `keep_backup = dest.exists()` for new aliases;
`_probe_if_needed` probes orphan sessions with caller credentials; headless
`_collect_code` requires `--code` and rejects empty values;
`PhoneCodeEmptyError` → exit 3. Minor: `accounts show` no longer creates a
stray `.lock` when the session is missing. Docs: plan Task 5 audit-before-
promote order, `CONTEXT.md` in MAP.md, ADR-0042 §8/§11 (NO_SEND rationale
without false ADR-0040 cite; exit-4 lookup fork), CONTRACT synopsis drops
`--code` from the primary login line, guide headless notes, CHANGELOG Fixed.
**Decided:** unknown alias on `accounts show|remove` stays exit 4
(registry lookup); `--account` stays exit 3 (config resolution) — documented
fork, not a silent drift.
**Learned:** `feature-dev:code-reviewer` subagents cannot run Bash — the
orchestrator must materialize diffs/worktrees before sending them.
**Next:** push `fix/adr-0042-review-fixes` onto the release PR tip and
re-run the stacked merge.

## 2026-07-24 — Telegram Devices labels tgcli sessions clearly (Composer)
**Did:** regular and staged-login Telethon clients now send one shared device
identity: `device_model=tgcli`, OS family, and package version. Added exact
constructor boundary tests, ADR-0042 §15, accounts guide and changelog notes.
Live check on the secondary `teamsyncsage` authorization returned
`device_model=tgcli`, `system_version=Darwin`; release connections report
`app_version=1.2.0`.
**Decided:** stable product identity is safer than Telethon's architecture-only
default (`arm64`), which made live device cleanup ambiguous.
**Learned:** Telegram updates the current authorization metadata on the next
connection; existing tgcli sessions do not need reauthorization just to gain
the clearer label.
**Next:** restore the accidentally terminated `recklessou` session if needed,
mark #46–#48 ready, then merge the stack and tag `v1.2.0`.

## 2026-07-24 — ADR-0042 live acceptance on recklessou (Composer)
**Did:** live-acceptance against secondary account RecklessOU via throwaway
`tmp-login` (never `main`). QR deep link: `open` succeeds on
`ru.keepcoder.Telegram`, token recreate fires repeatedly, but no confirmation
sheet appears — fell back to phone path per ADR/plan. Phone path: wrong code
→ exit 3 with attempt kept; correct code authorized and promoted; `accounts
show` + `dialogs --limit 1` worked. `--force` without flag refused (exit 2);
`--force --phone` replaced the session and left exactly one `.session.bak`.
`accounts remove --confirm` deleted config/session/bak; recklessou remained
usable. Caught and fixed a live bug: post-promote second `disconnect` in
`unauthorized_client` crashed with `sqlite3.OperationalError: no such table:
entities` (exit 1 despite successful promote); now skips disconnect when
already closed. Owner later confirmed the cloud-password native dialog **did**
appear and succeed during `recklessou` reauthorization after the accidental
session termination; earlier live notes that Telegram never asked for 2FA were
wrong for that recovery path.
**Decided:** keepcoder macOS client does not present `tg://login` confirmation
sheet; phone fallback is the proven recovery path on this machine. Owner still
terminates the extra device in the Telegram app.
**Learned:** CLI success must be judged after context-manager cleanup, not only
after promote; double-disconnect after moving the staged SQLite is fatal.
Native osascript password dialog works for 2FA on phone-path continue.
**Next:** owner terminates leftover device(s) in Telegram Settings → Devices;
merge plan → #46 → #47 → #48; tag `v1.2.0`.

## 2026-07-24 — ADR-0042 final CLI blocker fixes (Composer)
**Did:** added CLI regression coverage proving `accounts login --continue`
without an explicit timeout reaches attempt lookup instead of being blocked by
the global timeout default. `accounts remove --confirm` now creates the
sessions directory before taking its lock, so a configured account with no
state tree is removed cleanly with session/backup reported absent. Full gate:
855 passed, 9 skipped; ruff, format, pyright, coverage, architecture, docs
green.
**Decided:** suppress the global timeout default specifically for login
continuations; explicit `--timeout` remains rejected by preflight.
**Learned:** direct command-function tests did not cover the CLI default
backfill order, and remove's held-lock fix needed an empty-state-tree case.
**Next:** owner live acceptance on a secondary throwaway alias.

## 2026-07-24 — ADR-0042 independent review fixes (Composer)
**Did:** closed the independent Spec+Standards findings on the login stack.
Promotion restores the destination from `.bak` if the second rename fails;
new aliases must match `^[A-Za-z0-9_-]+$`; `accounts-login` writes
`outcome=started` before creating an attempt; password-step `FloodWait`
maps to exit 5; QR `--continue` only when `next=password`; `accounts remove`
holds the session lock across delete; preflight rejects `--code` without
`--continue` and `--timeout`/`--qr-format` with `--continue`; osascript
secrets keep leading/trailing spaces; `store stats` counts each login-pair
file; CONTRACT documents login `--plain` columns and the logins count rule.
Full gate green after the fixes.
**Decided:** keep audit-before-RPC as a started record plus authorized-before-
promote; do not invent a second audit subsystem. Live acceptance on a
secondary throwaway alias remains the owner merge gate (ADR-0042 §13).
**Learned:** the promote atomicity test had been asserting a torn destination
as acceptable — the plan required destination intact, and the test was the
bug.
**Next:** owner live acceptance on a throwaway alias; then merge plan → #46 →
#47 → #48 and tag `v1.2.0`.

## 2026-07-24 — ADR-0042 Slice 4: store logins + docs + release 1.2.0 (Cursor)
**Did:** `store stats`/`cleanup` learn `logins/{live,expired}` (json + staged
session) and report `session_backups` (never deleted). Guide pages
(`accounts`, `safety`, `store`, `doctor`), `SKILL.md`, `ISSUES.md`
(ACCOUNTS-001 closed, backup note kept), `PROPOSALS.md` (show/remove
graduated). Version `1.1.3` → `1.2.0` with CHANGELOG section naming
ADR-0042. Architecture ceilings unchanged from Slices 2–3.
**Decided:** owner-declared minor per ADR-0042 §14 / ADR-0038; tagging
`v1.2.0` remains the owner's post-merge action. Live acceptance against a
secondary account remains the merge gate and was not run in this cloud
environment.
**Learned:** check-docs fails closed on invented guide flags — routing new
commands through the live parser first kept the guide gate green.
**Next:** independent whole-diff Spec + Standards review from the
merge-base; owner live acceptance on a throwaway alias.

## 2026-07-24 — ADR-0042 Slices 2–3: QR + phone login (Cursor)
**Did:** implemented Tasks 3–8. New modules `desktop.py` (osascript/open
escape hatch), `authclient.py` (unauthorized client + probe),
`login_state.py` (`logins/` attempts + atomic promote),
`commands/login.py` (QR default, phone+code, `--continue`, cloud password
via dialog/`--password-stdin` never argv). `mask_phone` in `formatting.py`.
CONTRACT §10 login shapes and exit mapping. Architecture ceilings raised to
measured `cli.py` 293, `parser.py` 496, `preflight.py` 231. Gate: 840
passed / 9 skipped, ruff/pyright/coverage/architecture/docs clean.
**Decided:** no design drift from ADR-0042. Password collection refuses the
automatic stdin fallback unless `--password-stdin` (headless → `next:
password` at exit 0).
**Learned:** `qr.wait` timeout vs overall `--timeout` needs an explicit
deadline loop with `recreate`; a single wait is not enough.
**Next:** Slice 4 — `store` logins bucket, guide/SKILL/ISSUES updates, release
`1.2.0`.

## 2026-07-24 — ADR-0042 Slice 1: `accounts show` / `remove` (Cursor)
**Did:** implemented offline account lifecycle from
`docs/superpowers/plans/2026-07-24-accounts-login.md` Tasks 1–2.
`tg accounts show ALIAS` reports config presence, session path/size/mtime,
non-blocking lock probe, `.bak` slot, and always-null `authorized`.
`tg accounts remove ALIAS [--confirm] [--keep-session]` is report-only
without `--confirm` (exit 2), refuses `default_account` and a held lock,
rewrites config atomically at mode `0600` preserving unrelated content, and
audits `accounts-remove` before deletion. `TGCLI_NO_SEND` does not apply;
`--readonly` blocks only `--confirm`. CONTRACT §10 updated; architecture
ceilings raised to measured `cli.py` 267 and `parser.py` 461. Tests in
`tests/test_commands_accounts.py`.
**Decided:** followed ADR-0042 / the plan; no design change. Config section
removal is line-oriented text filtering (not a TOML round-trip) so comments
and sibling accounts survive.
**Learned:** entry-layer ceilings were already exact; Slice 1 alone forced
the first deliberate raise.
**Next:** Slice 2 — client seam, `logins/` state, QR login path (Tasks 3–6).

## 2026-07-24 — ADR-0042: `tg accounts login` grilled and planned; three loose ends closed (Claude Opus 4.8)
**Did:** closed the administrative tail — issue #34 (drafts shipped in 1.1.1),
`CHANGELOG.md` compare links for `1.1.2`/`1.1.3`, and the inaccurate README
claim that "every mutation is two invocations" (dialog-state mutations run in
one). Then a full owner grill over ACCOUNTS-001 → new root `CONTEXT.md`
(account/authorization vocabulary), `docs/decisions/ADR-0042-accounts-login.md`,
and the scoped plan `docs/superpowers/plans/2026-07-24-accounts-login.md`
(4 slices, 11 tasks). Docs-and-plan only, no source changed; gates green
(785 passed, architecture and docs checks pass).
**Decided:** twelve decisions, all in ADR-0042. Headline: QR login by default
with a phone+code fallback; the cloud password collected through a native
`osascript` dialog with a stdin fallback, never through `argv`; a login attempt
is *not* a preview and lives in its own `logins/`; `sessions/<alias>.session`
gets exactly one writer, promotion by atomic rename after Telegram confirms;
`--readonly` blocks, `TGCLI_NO_SEND` does not; an incomplete handshake is exit 0
plus `"next"`, not a new exit code; scope widened to `show`/`remove` so the
account lifecycle closes; ships as the owner-declared minor `1.2.0`.
**Learned:** four things that only came from checking. (1) All four entry-layer
files — `cli.py` 254, `parser.py` 439, `preflight.py` 203, `dispatch.py` 245 —
sit *exactly* at their `check-architecture.py` ceilings, so this feature cannot
add a line without a deliberate ceiling raise. (2) The desktop client here is
`ru.keepcoder.Telegram`, not Telegram Desktop: no `tdata`, no supported session
extractor, so "just copy the session from the app" is closed on facts before
judgement. (3) Telethon's own `QRLogin.url` docstring states the `tg://login`
URI is meant to be opened by a logged-in Telegram app — that is what makes the
deep link a real design, though the macOS handler registration is still
unproven and is called out as the plan's one live-only assumption. (4) The
ISSUES.md mitigation "keep the config and state dirs in a backup" is **still
unmet** (`AutoBackup = 0`, destination fails to mount) — recovery rests entirely
on this feature.
**Next:** Cursor executes the plan, starting with Slice 1 (offline
`accounts show` / `remove`), which ships on its own.

## 2026-07-24 — ADR-0041: user-facing guide, 22 pages + fail-closed docs gate (Claude Opus 4.8 orchestrating 5 Sonnet subagents)
**Did:** owner asked to split user-facing docs out of the engineering tree.
Wrote ADR-0041 + index row, `docs/guide/README.md` (index), and orchestrated
five parallel Sonnet subagents writing 22 pages (Start / Reading / Writing /
Data / Operations), each handed a written page spec, the captured `--help` for
all 51 command paths, and a hard "verify every flag, invent nothing, never
touch Telegram" rule. Added `scripts/check-docs.py` — walks the live argparse
tree and fails closed on unknown flags, unknown `tg <command>`, or dead
relative links — and wired it into CI. Updated MAP.md and the README doc
table. Gate: 785 passed / 9 skipped, ruff clean, ruff format clean, pyright
0 errors, coverage OK (23 namespaces), architecture passed, check-docs 23
pages / 0 problems.
**Decided:** ownership boundaries are explicit in the ADR — CONTRACT.md stays
versioned law and **wins over the guide on any conflict**; SKILL.md stays the
agent routing table and is not replaced; `docs/CLONE.md` stays closed history
with `guide/clone.md` as the current page. The guide is only acceptable
because it is machine-checked; without `check-docs.py` this ADR would have
been a mistake.
**Learned:** the checker earned its keep immediately. It caught an invented
`--dest-type` flag in a generated page, and cross-checking `clone.md` against
CONTRACT §11 exposed a **pre-existing docs bug**: README and the SKILL.md
frontmatter both claimed clone supports "non-forum supergroups", which has
been stale since ADR-0022 added forum topics — the contract accepts forum and
non-forum megagroups, legacy basic groups, and dialogs. Both fixed here. Two
subagents independently flagged the same discrepancy, which is what made it
credible. Also corrected `overview.md`'s claim that reads touch no local state
(they write the metadata-only invocation journal).
**Next:** owner reviews the three stacked PRs, merged bottom-up:
`claude/devlog-adr-0040-close` → `claude/readme-wacli-style` →
`claude/docs-user-guide`. Owner decided **no `LICENSE` file for now**; the
repo-metadata command (topics) stays optional while the repository is private,
since GitHub does not index private repositories by topic.

## 2026-07-24 — README restyled after wacli; docs-site question answered (Claude Opus 4.8)
**Did:** owner asked for GitHub presentation in the style of
[openclaw/wacli](https://github.com/openclaw/wacli). Studied that README and
`wacli.sh` (custom static site built by `scripts/build-docs-site.mjs` from
`docs/*.md`, published by `.github/workflows/pages.yml` behind a `CNAME`).
Rewrote `README.md` to the same shape — banner, one-line pitch, third-party
disclaimer, Features, Install, Quick start, Documentation table, Configuration
(env + exit-code tables), one deep-dive section (preview → commit, the analogue
of wacli's history-backfill section), Status, Credits, Maintainers. Added
`docs/assets/readme-banner.svg` (Telegram-blue terminal card, no external
fonts, renders in GitHub light and dark). Docs only; no code, no contract, no
version bump. Gate: 785 passed / 9 skipped, ruff clean, architecture check
passed.
**Decided:** no docs site for now — recommended against porting the wacli.sh
pattern while the repo is private (Pages on a private repo needs a paid plan
and would publish a public site for a private tool) and while the readership is
one owner plus agents that read `SKILL.md` and `docs/CONTRACT.md` straight off
disk. Left the README's documentation table as the index instead. Deliberately
did **not** add a `LICENSE` section or file — no license exists in the repo and
picking one is the owner's call.
**Learned:** `tg send --preview --json` has no `rendered` field (CONTRACT §5
shape is `preview_id`/`to`/`text`/`file*`/`reply_to`/`topic`/`silent`/
`expires_at`) and a phone number is a `tg resolve` input, not a general chat
ref — both drafted wrong from memory and corrected against CONTRACT.md and
`chatref.py`. Verify README claims against the contract, not against SKILL.md
prose.
**Next:** owner decides on the repo-metadata command (topics/description) and
on whether a `LICENSE` file should exist; if the repo ever goes public,
re-open the docs-site question — the wacli approach is ~300 lines of build
script over the `docs/*.md` that already exist.

## 2026-07-24 — Review Cursor's ADR-0040 work, ship 1.1.2/1.1.3 (Claude Opus 4.8)
**Did:** reviewed PR #40 against the plan — all 4 tasks executed, gate
reproduced locally (782 → 785 passed). Found and fixed four defects in
`1ed3bda`: (1) a preview with an unreadable `expires_at` was classified
`expired` and reaped, so a torn mid-write preview could be deleted by a
concurrent cleanup — now falls back to mtime, as the plan required for the age
anchor only; (2) `store cleanup --confirm` was gated on
`enforce_mutation_allowed`, so `TGCLI_NO_SEND=1` blocked local housekeeping —
split out `enforce_local_mutation_allowed` (readonly only); (3) the new
`preview_perms_ok` check turns every existing install `ok: false` on legacy
`0644` previews with no remedy in sight — `doctor` now prints
`tg store cleanup --confirm` to stderr; (4) the slice-2 DEVLOG entry claimed a
`_writable` bug that never existed on main — corrected. Merged #40 (#39 was a
strict subset), tagged, and published the first GitHub Releases.
**Decided:** tags point at the version-bump commits (`7f0e304` → v1.1.2,
`f65aa44` → v1.1.3), not the merge commit — both tags had landed on `1ecefe4`
and were force-moved while the release was minutes old and unpulled.
**Learned:** the repo had **7 tags and zero published Releases**, so GitHub
showed no version at all — a tag is not a Release. v1.1.2/v1.1.3 are now
published from the CHANGELOG sections; v1.0.0/v1.1.0/v1.1.1 remain tag-only.
**Next:** optionally backfill Releases for the older tags; ADR-0040's deferred
items (`--events` → FEED-001, `tg spec`) stay closed until their triggers fire.

## 2026-07-23 — ADR-0040 slice 2: offline-first `tg doctor` (Cursor)
**Did:** executed plan Task 4. `tg doctor` is offline by default (session file,
lock, state writability, preview/audit perms, `state_size`); live `authorized`
probe only under `--connect`. Offline `authorized` is `null`; plain status
`unknown`. CLI skips network timeout wrap when offline. CONTRACT §5.1 updated;
patch 1.1.3. ACCOUNTS-001 companion: a broken session still gets a local
diagnosis. Full gate green.
**Decided:** `ok` from local checks only when offline; with `--connect`, `ok`
also requires `authorized`. `state_size` is informational and never fails `ok`.
**Learned:** moved `_writable`'s `return True` out of the `try` to below the
`finally` — behaviourally identical (the old `return True` already ran before
the cleanup), a readability nudge, not a bug fix.
**Next:** owner tags 1.1.2 / 1.1.3; independent Spec+Standards review of the
whole-diff before merge.

## 2026-07-23 — ADR-0040 slice 1: `tg store` (Cursor)
**Did:** executed plan Tasks 1–3. `tg store stats` inventories previews
(live/expired/spent/pending), audit/invocations/sessions/clones/downloads, and
relic dirs. `tg store cleanup` dry-runs by default; `--confirm` deletes spent
`.used` + expired `.json` only; `--include-pending` only when far past TTL;
`--older-than` Nd/Nh; blocked under `--readonly` (exit 2). Preview writes and
confirmed cleanup tighten modes to `0600`; `stats` reports
`previews_world_readable`. CONTRACT §5.05, MAP, patch 1.1.2. Full gate green.
**Decided:** held ADR-0040 boundaries — audit log and sessions never enter the
deletable set; relics report-only; dry-run does not chmod (mutation stays behind
`--confirm`).
**Learned:** argparse `argument_default=SUPPRESS` on global parents does not
suppress subparser `store_true` defaults; architecture ceilings must move with
cli/parser growth in the same commit as the fixture mirror.
**Next:** Slice 2 — `tg doctor` offline by default, live checks behind `--connect`.

## 2026-07-23 — ADR-0040: wacli-review adoption scope (Claude Opus 4.8)
**Did:** owner-driven grilling + domain-modeling session over the four wacli
items in `docs/PROPOSALS.md`. Wrote `ADR-0040` (adopt `store stats|cleanup`
and offline-by-default `doctor --connect`; defer `--events` and `tg spec`),
added its README index row, marked the four PROPOSALS statuses, and wrote the
scoped plan `docs/superpowers/plans/2026-07-23-wacli-store-doctor.md` (2 slices,
4 tasks) intended for execution by another agent (Cursor). Docs only — no code.
**Decided:** ADR-0040. `store cleanup` reaps spent previews (`.used`) and
expired `.json` only; the **audit log and sessions are untouchable by design**
(a cleanup that could erase the audit trail hands an agent a cover-tracks
button, against ADR-0005). `.pending` protected (ADR-0028 `random_id`). Relics
reported by `stats`, never auto-deleted. `doctor` goes offline-first, live
checks behind `--connect` (companion to ACCOUNTS-001). Deferred with triggers:
`--events` → FEED-001 (event stream is a CONTRACT §3 contract, design once);
`tg spec` → demonstrated drift pain + explicit overturn of ADR-0028 (ADR-0034
already weakened its objection).
**Learned:** terminology trap — "draft" is taken by ADR-0039 (Telegram drafts),
so the safety record stays **preview**; a terminal one is a **spent preview**,
not a "burnt draft". Measured state: 51/59 previews are spent `.used` bodies at
`0644`, kept forever — the privacy driver; cleanup tightens them to `0600`.
**Next:** Cursor executes the plan (`docs/superpowers/plans/2026-07-23-wacli-store-doctor.md`)
slice by slice — `store` first, then `doctor --connect` — TDD per task per
ADR-0026, patch release per ADR-0038.

## 2026-07-23 — Ship 1.1.0 + 1.1.1, tag the release stack (Claude Opus 4.8)
**Did:** owner declared the milestone, so finished the Codex-prepared release
stack. Merged `claude/release-1.1.0` (#36 → main, merge `ccb690d`), the stacked
`claude/drafts` (#37, retargeted to main, merge `c24d857`), and the independent
`claude/wacli-review` (#35, resolving one DEVLOG top-of-log conflict by keeping
both workstreams). Tagged `v1.1.0` on `ccb690d` and `v1.1.1` on `c24d857` from
their merge commits and pushed both. This commit drops the `(pending tag)`
markers now that the tags exist and repoints the CHANGELOG compare links at the
tags.
**Decided:** tags live on the first-parent merge commits so `v1.1.0` is the
release without drafts and `v1.1.1` is release + drafts; the CHANGELOG must not
keep a `(pending tag)` marker after the tag is real.
**Learned:** a stacked feature branch rebased onto its base merges into main
conflict-free because it already contains the base's doc entries; only the
independent branch collides on the append-newest-first DEVLOG head.
**Next:** none required — 1.1.x is shipped and tagged. Future work stays behind
the ADR-0026 gate (MSG-001 / FEED-001 / ACCOUNTS-001).

## 2026-07-23 — Repair the wacli review branch (Codex)
**Did:** corrected the reviewed branch's five factual/design defects: gap
recovery no longer claims `read --after-id` can reconstruct old edits or
deletions; account selection describes tgcli's actual three-step resolution;
offline account/doctor checks no longer claim server authorization; the docs
coverage statement includes `calls` and `companion integrations`; and the
ADR-0038/CHANGELOG statement is explicitly a sibling-branch dependency, not
current `main` behavior. Final independent review also removed a premature
deletion-event JSON shape and corrected doctor's conditional live-probe
description. Restored the missing original-session DEVLOG entry.
Repo-local final gate: `.venv/bin/pytest -q` — `736 passed, 8 skipped in
4.97s`; Ruff check passed; Ruff format reported `113 files already formatted`;
Pyright reported `0 errors, 0 warnings, 0 informations`; coverage reported
`coverage OK: 23 namespaces`. The equivalent `uv run` gate could not start
because approval-service usage limits blocked access to uv's shared cache; the
branch's existing GitHub CI was green before these docs-only fixes.
**Decided:** FEED-001's exact gap schema remains an ADR decision. The backlog
may state required truthfulness and recovery limits, but it must not freeze a
JSON contract before that ADR.
**Learned:** an `after_id` replay closes creation gaps only; it cannot recover
edits or deletions whose message IDs predate the cursor.
**Next:** run independent whole-branch review, then merge only after the
ADR-0038 wording remains truthful against the final branch graph.

## 2026-07-23 — Review wacli for transferable decisions (Cursor)
**Did:** reviewed wacli's documented surface against tgcli and recorded
unvetted agent/account/media ideas in PROPOSALS plus lock-contention and
event-recovery inputs under the already-deferred FEED-001. No product behavior
changed.
**Decided:** every new item remains behind ADR-0026's explicit owner + ADR +
scoped-plan gate; FEED-001 still needs its own design decision.
**Learned:** a long-running feed conflicts with tgcli's exclusive per-session
lock, and WhatsApp's local-mirror solutions do not transfer wholesale to
Telegram's server-side history/search model.
**Next:** independently verify every wacli disposition and current-branch
dependency before merge.

## 2026-07-23 — ADR-0039 topic-only retry correction (Codex)
**Did:** normalized the desired retry snapshot through the exact
`InputReplyToMessage` shape sent to Telegram. A topic-only draft stores its
topic as `reply_to_msg_id` with no `top_msg_id`; the regression simulates a
save that completed before result auditing failed and proves its retry is
accepted rather than misclassified as a human overwrite.
**Decided:** retry equality follows the actual TL request, not raw CLI flag
names, while the full observed-state mismatch still fails closed.
**Learned:** `--topic` alone is intentionally encoded differently from a
reply-within-topic, so comparing unnormalized CLI payload fields is unsafe.
**Next:** final independent re-review of the stale-preview guard.

## 2026-07-23 — ADR-0039 full-state stale-draft correction (Codex)
**Did:** final review found that the first stale-preview guard compared only
text. Draft previews now persist a JSON-safe internal snapshot of text, reply,
topic, and every Telethon formatting entity; commit re-reads immediately before
`saveDraft`, rejects any observed mismatch, and still accepts a retry whose
complete state already equals the requested one. Added same-text reply and
formatting-entity regressions; public preview JSON is unchanged.
**Decided:** ADR-0039 now explicitly records the bounded guarantee: Telegram
has no conditional-save/version token, so the final read→save race is a
residual risk rather than an untruthfully claimed CAS guarantee.
**Learned:** checking text alone treats changed reply/thread metadata and rich
formatting as invisible, exactly where a human's prepared draft needs safety.
**Next:** independent re-review of the full-state guard, then rebase onto the
corrected release stack and tag only after merge.

## 2026-07-23 — ADR-0039 drafts whole-diff review corrections (Codex)
**Did:** independently reviewed the `claude/release-1.1.0...claude/drafts`
drafts slice on Spec and Standards axes, then fixed three confirmed safety and
contract defects. `SaveDraft` now treats Telegram's idempotent
`MessageNotModifiedError` as success; commit re-reads the draft and refuses a
human change made after preview, while accepting a retry whose requested draft
already exists; and `draft set --commit` now rejects an extra `--format` flag.
Added permanent public-seam regressions and documented the stale-preview rule
in CONTRACT. Raised the reviewed `preflight.py` architecture ceiling to its
actual 203 lines with the paired architecture regression.
**Decided:** these are narrow corrections required by ADR-0039's existing
preview safety and commit-only contract, not a new behavior decision or ADR.
The inherited ADR-0038 changelog wording and `v1.1.1` tag remain release-merge
work owned by the stacked release flow.
**Learned:** `messages.saveDraft` has both bare-`Bool` and
`MessageNotModifiedError` no-op paths; a retryable preview must distinguish a
human overwrite from a save that already completed before result auditing.
**Next:** rebase this committed slice onto the corrected 1.1.0 release, then
tag `v1.1.1` only after the feature is merged.

## 2026-07-23 — wacli review: backlog items + FEED-001 blocker (Claude Opus 4.8)
**Did:** owner-requested review of [wacli](https://wacli.sh/) (openclaw's
WhatsApp CLI, sibling of the gogcli lineage) for what transfers to tgcli, with
a second opinion from GPT as input. Docs only, no code. `docs/PROPOSALS.md`:
new "Agent surface" subsection — `tg store`, `--events`, `doctor --connect`,
`tg spec`, plus a checked-and-rejected note. `docs/ISSUES.md` FEED-001: a
session-lock blocker and a design-input block (deletion tombstones, loud gaps,
story-viewer scope warning). Written on `claude/drafts` because that branch was
checked out and switching under a concurrently running session was the larger
risk.
**Decided:** everything stays behind the ADR-0026 gate — proposals, not work.
wacli's SQLite+FTS5 mirror, `sync --follow`, and in-tool webhooks are non-goals:
they compensate for WhatsApp having no server-side search and no readable
history, which Telegram has. `tg spec` is recorded as an explicit re-proposal
against ADR-0028, not a fresh idea.
**Learned:** three findings that only came from checking instead of assuming.
(1) FEED-001 as agreed is unbuildable: `LOCK_EX | LOCK_NB` held for a whole
invocation means a `tg changes --wait 30` poller starves every other command on
the account — the feed would break the workflow it exists for; wacli solved the
same collision with send-delegation, which for us is a daemon by another name.
(2) `stories.getStoryViewsList` has been read-allowlisted since ADR-0010, so the
story-viewer lead workflow needs no new subsystem at all. (3) Nothing prunes
`~/.local/state/tgcli`: 51 of 59 preview files are burnt `.used` bodies kept
forever at `0644`, `audit.jsonl` is 1.1 MB unbounded, and 340 KB belongs to the
removed `tg mirror`. Also: the second-opinion review cited wacli accurately but
misstated tgcli's own state (claimed `gap` was already in the agreed FEED-001
shape; proposed an `accounts add` duplicating `accounts import`) — same lesson
as 2026-07-18, verify review claims against the repo.
**Next:** run the real lead scenario against `tg api stories.getStoryViewsList`
and see what is actually missing before opening FEED-001 or ACCOUNTS-001.

**Addendum (second pass over remaining wacli pages).** Added to PROPOSALS:
`accounts show`/`remove` (our surface has `import`+`list` but not the other
half; both belong in the ACCOUNTS-001 PR, and `show` is the offline branch of
`doctor --connect`), and a `kind: temporary|permanent` field on bulk-media
`failed` (stateless take on wacli's unavailable-media dedup — no local DB
needed). Corrected provenance: wacli's `spec` is a documentation page, not a
command, so `tg spec` is my own idea prompted by the review, not an import —
fixed the wording in PROPOSALS. Rejected on inspection: `--read-only` media
with `--output` (we are already stricter — download never sits behind the gate
because it does not mutate Telegram); `history coverage/backfill` (cures
WhatsApp's unreadable history, which Telegram does not have); contacts
aliases/tags and `import-system` (workflow data / platform binding, belong in
an external `tg-agent`, not the core).

## 2026-07-23 — ADR-0039 review fixes (Composer)
**Did:** closed independent Spec+Standards findings on `claude/drafts`:
audit timing + `TGCLI_NO_SEND` tests; `_save_draft` asserts bare `bool`
result; commit re-fetches via `draft show` so `date`/reply fields are real;
FakeClient mirrors `SaveDraft` into peer dialogs; live smoke asserts md
strip (`**` gone), `--reply-to`, and non-null `date`. Gate: `755 passed,
9 skipped`; live draft smoke green.
**Decided:** commit JSON is a post-save re-read, not a local synthesis.
**Learned:** instance `__call__` assignment is ignored by Python; capture
RPC results via a FakeClient subclass.
**Next:** re-run Spec+Standards on the fix commit if desired; open PR;
tag `v1.1.1` after merge.

## 2026-07-23 — Message drafts ADR-0039 / v1.1.1 (Composer)
**Did:** implemented `tg draft set|show|clear|list` on `claude/drafts`. Reads
landed as `draft.show`/`draft.list` in `read_ops`; set/clear use preview→commit
with `old_text` (`expected_kind` `draft-set`/`draft-clear`). Own draft JSON
object; boundary test asserts `SaveDraftRequest` + Bool. CONTRACT/SKILL/MAP/
CHANGELOG + patch bump to 1.1.1. Live smoke covers markdown set, show, no-op
set, clear, and clear-of-empty.
**Decided:** ADR-0039 as grilled — no `draft send`, no `--file` in v1; set
mirrors `send` format defaults.
**Learned:** Telethon `Draft` hides entities/`top_msg_id`; read path uses TL
`DraftMessage` directly. `utils.get_peer_id` rejects SimpleNamespace fakes —
resolve users/chats by peer type + id instead.
**Next:** independent Spec+Standards whole-diff review; tag `v1.1.1` after
merge.

## 2026-07-23 — Correct ADR-0038 release semantics (Codex)
**Did:** corrected the release records after an independent `main...HEAD`
review: the policy now applies to fixes as well as features, CHANGELOG states
that patches carry individual changes while the owner declares minors, and all
1.1.0 bullets name their governing ADR. Final repository-local `.venv` gate:
`736 passed, 8 skipped`; ruff check/format clean; Pyright 0 errors; coverage
23 namespaces. No behavior files changed.
**Decided:** ADR-0038 remains a release-policy decision, not a CLI contract
change; preserve the append-only DEVLOG record of the superseded mechanical
minor-bump wording and add this correction rather than rewriting history.
**Learned:** an ADR amendment can leave its original release note internally
consistent but contradicted by surrounding consumer and agent documentation;
the tag is also part of the release contract, not an optional follow-up.
**Next:** merge the release branch. After merge, create and publish the
`v1.1.0` tag from the merged release commit.

## 2026-07-23 — Release 1.1.0: changelog and version discipline (Claude Opus 4.8)
**Did:** cut the catch-up release. Added root `CHANGELOG.md` (Keep a Changelog,
one section per release, every bullet naming its ADR), bumped `1.0.0 → 1.1.0`
in `pyproject.toml` and `src/tgcli/__init__.py`, wrote ADR-0038 with its index
row, added the release rule to AGENTS.md doc discipline, and indexed
CHANGELOG.md in MAP.md. Gates: ruff check + format clean, `736 passed, 8
skipped`, `tg --version` → `1.1.0`.
**Decided:** ADR-0038 — semver is measured over `docs/CONTRACT.md`, so an
additive surface is a minor bump; one feature = one tagged release, with the
CHANGELOG section and version bump landing in the same commit as the feature.
1.1.0 is the only section that bundles several ADR waves (0028…0037).
**Learned:** the version had drifted for 93 commits and ten ADRs, so the notes
had to be reconstructed from `git log` — the exact cost rule 3 of ADR-0038
exists to prevent. Owner also opened two genuinely new features (Telegram
voice transcription, message drafts); neither appears anywhere in ISSUES.md or
PROPOSALS.md, and both are reachable in the pinned Telethon 1.44
(`messages.transcribeAudio`, `messages.saveDraft` / `client.get_drafts`).
**Next:** grill the transcription + drafts scope into an ADR — the open
questions are the `pending=True` async transcription result under a daemonless
CLI, premium/trial quota preflight, and whether transcription counts as a read
under `TGCLI_READONLY`.

## 2026-07-23 — PR #28 rebase and typed-discriminator review (Codex)
**Did:** completed the interrupted rebase of the read-operation registry onto
`main`, preserving both sides of the DEVLOG conflict, then reviewed the whole
diff on independent Spec and Standards axes. The review reproduced one real
defect: operation dataclasses accepted an override such as
`Dialogs(..., name="read")`, letting the string-keyed dispatcher select a
handler for the wrong variant. Added a red regression and made every operation
name an immutable `ClassVar[Literal[...]]`. Focused registry tests: 33 passed.
Final post-PR33 rebase gate: `736 passed, 8 skipped`; Ruff check/format passed;
Pyright reported 0 errors; coverage passed 23 namespaces.
**Decided:** the registry remains keyed by the documented operation names, but
the discriminator belongs to the closed typed variant and is not constructor
input. ADR-0034 and the public CLI contract remain unchanged.
**Learned:** a frozen dataclass does not make a defaulted discriminator safe
when callers can still replace it during construction.
**Next:** rebase this reviewed branch onto the just-merged PR #33, update PR
#28, wait for fresh green CI, then merge and delete the final feature branch.

## 2026-07-23 — PR #33 independent boundary review (Codex)
**Did:** reviewed the rebased quote-fallback split from `main` on both Spec and
Standards axes. Added red regressions for two confirmed defects: the
architecture-test ceilings had drifted from the checker, and resolver-owned
peer/cache and plan-transition helpers lived in `quote_fallback`. Moved stable
peer identity to `attribution.peer_key`, the generic upload-capable transition
to `transport.as_reuploaded`, and kept `quote_fallback` renderer-only. The new
tests bind the test fixture to the real ceilings and reject those resolver
helpers from the fallback module. Final gate: `703 passed, 8 skipped`; Ruff
check/format passed; Pyright reported 0 errors; coverage passed 23 namespaces.
**Decided:** ADR-0037's renderer boundary is literal: native resolution must
not depend on `quote_fallback`. The fallback ceiling ratchets from 149 to its
reviewed post-fix size of 127; `quotes.py` remains at 365.
**Learned:** duplicating a budget registry in a test can silently weaken the
fixture even while the repository-level architecture test remains green.
**Next:** update PR #33, wait for fresh green CI, then merge and delete its
branch before rebasing the remaining PR #28.

## 2026-07-23 — Split quote fallback rendering from the resolver (Composer)
**Did:** ADR-0037. Extracted `clone/quote_fallback.py` (source label, blockquote
prefix, peer label, fallback plan, `apply_body`, `drop_stale_quote`) from
`clone/quotes.py`, which keeps reachability, cross-leg walks, thread
placement, `resolve`, and `send_with_degrade`. `commands/clone.py` and tests
import the owning module — no re-exports. Architecture ceilings: `quotes.py`
365, `quote_fallback.py` 127 (down from the 500 monolithic
bump). MAP + ADR index updated. Behaviour unchanged; existing quote tests are
the proof. Rebased onto the merged #31 after review: the split now carries the
foreign-parent guard in `_place_thread` and the `placement` argument that lets
`fallback_plan` keep a rejected send's resolved thread. Independent review
moved peer identity to `attribution` and the generic reupload-plan transition
to `transport`, keeping native resolver paths out of the fallback renderer.
**Decided:** two jobs, two modules — classify (`replies`) → resolve (`quotes`)
→ render loss (`quote_fallback`). Rejected a three-way peers split as shallow
(ADR-0034).
**Learned:** the live-run fixes that grew the file were almost all fallback
shaping; they never needed the async resolver context. The review fixes split
the same way: the guard stayed with the resolver, the placement argument went
with the renderer.
**Next:** independent Spec + Standards review of PR #31 (and this stacked
seam PR) before merge.
## 2026-07-23 — Review of the quote-reply branch: two resolver defects (Claude Opus 4.8)
**Did:** reviewed PR #31 against its plan and ADR-0036, then fixed the two
defects the review found in `clone/quotes.py`. (1) `_place_thread` walked
`reply_to_msg_id` against the source discussion group even when the header
named another peer, so a foreign parent id colliding with an anchor produced
a native quote reply at an unrelated destination post *and* the fallback
prefix — ADR-0036 §4's silent failure. The walk now takes `reply_to_top_id`
alone unless the parent lives in this group. (2) `degrade_to_fallback` reset
`reply_to` to `None`, discarding the thread anchor `_place_thread` had already
resolved; `dest_for(top)` cannot recover it because a discussion top is an
auto-forward anchor that never enters the leg's map, so a rejected foreign
quote landed outside its thread. Placement now carries over. Also removed a
no-op `if`/`pass` block, an unused `ctx` parameter, and the duplicated
forbidden-peer error tuple. Two regression tests, both verified red against
the pre-fix module. 701 passed, 8 skipped; ruff + pyright + architecture green.
**Decided:** absorb the churn inside the reviewed 500-line ceiling for
`quotes.py` instead of a fourth bump — the file is 498 after folding
`_peer_label` onto `_peer_cache_key` and inlining `_input_peer`.
**Learned:** the branch's own quiet-trap test for source 2374 passes with the
foreign-parent walk intact, because its context has no `source_group`; the
trap only fires on the discussion leg, which is exactly where it lives.
**Next:** the `replies.target` classification is recomputed two to three times
per batch (decide → resolve → degrade), and `decide`'s `reply_flattened` is
provisional until `resolve` runs — worth threading the classification through
when the seam gets the look the previous entry asked for.

## 2026-07-23 — Clone quote replies: live run and two field fixes (Claude Opus 4.8)
**Did:** consolidated four stray branches into `cursor/clone-quote-replies`
(the other three were strict subsets; PR #30 was auto-closed by the rename
and replaced by #31). Ran the owner-approved live verification against clone
`4fa28c42…` after backing up its state: the 2374 fallback by `--limit 1`,
then the full catch-up to discussion source 2405 (`copied: 26`,
`more: false`). Two defects surfaced only in the live run and are fixed
here: (1) an unopenable quote peer was labelled `id 2275285084` — the title
is in the `ChannelForbidden` entry Telegram ships with the quoting message,
so `quotes._peer_title` reads it from there; (2) `QUOTE_TEXT_INVALID`
crashed the whole batch, now `quotes.drop_stale_quote` retries once without
the fragment and keeps the reply link. Fallback source line gained the
`Переслано от:` label at the owner's request. CONTRACT updated for both.
699 passed, 8 skipped; ruff + pyright + architecture green.
**Decided:** a rejected quote drops the fragment and keeps the reply rather
than rendering the text fallback — the link is valid, only the stale
fragment is not, and native threading is worth more than a fragment the
parent no longer contains. Live-run permission for `tg clone sync` lives in
gitignored `.claude/settings.local.json`, not the committed project file:
write access to the owner's Telegram is not a team-wide rule.
**Learned:** Telegram carries a banned-from channel's title in the enclosing
history response — resolving the bare `PeerChannel` raises
`ChannelPrivateError`, so the response is the only place the name exists.
Telethon has no named class for `QUOTE_TEXT_INVALID`; it arrives as a plain
`BadRequestError` and must be matched on the message prefix. GitHub's branch
rename API did not retarget the open PR — it closed it.
**Next:** `quotes.py` grew 380 → 500 lines across three ceiling bumps in one
session; the seam wants a look before more lands on it.

## 2026-07-23 — Clone quote replies slice 3 (Composer)
**Did:** implemented ADR-0036 slice 3. Sync collects `quote_flattened`
`{"id","peer","reason"}` rows from `TransportPlan`; a run that planted any
fallback finishes work, writes the result document, and raises
`PartialFailure` with `PolicyError` exit 2. JSON gains `quote_flattened`;
plain gains `quote_flattened_count`. CONTRACT §clone-sync updated (reject
sentence for reply-from/media/cross-peer removed). `PartialFailure` carries
optional `rows` for plain emit. Architecture ceilings: `cli.py` 215,
`clone.py` 910. Inverted foreign-peer sync tests to expect exit 2 + rows.
**Decided:** empty `quote_flattened` still exits 0; only planted fallbacks
are a partial failure (ADR-0036 §5).
**Learned:** ruff format expands a one-line `emit_plain(...)` past the old
`cli.py` ceiling, so the reviewed budget had to move with the rows seam.
**Next:** slice 4 leftovers are already mostly landed (MAP/quotes ceiling);
full gate + owner-gated live smoke of the wedged clone — do not mutate
Telegram without owner review.

## 2026-07-23 — Clone quote replies slice 2 (Composer)
**Did:** implemented ADR-0036 slice 2. Added `clone/quotes.py` async
resolver (mapped same-leg, mapped cross-leg via post map → destination
anchor, foreign-peer reachability cache, unreachable/rejected → rendered
fallback using `attribution.with_prefix` for UTF-16 shifts). Folded
`comments._remap` into `quotes.resolve` / `_place_thread`; both legs run
the same resolution step from `copy_batch`. Wired `send_with_degrade` for
reachable-then-rejected foreign quotes. Quiet-trap tests for 😴 UTF-16
offsets and source-2374 wrong-map (discussion 1244). Updated MAP,
architecture ceilings (`quotes.py` 380, `clone.py` 900).
**Decided:** `TransportPlan` gains internal `body_prefix` /
`quote_flattened` seams for slice 3 reporting without changing CONTRACT
exit semantics yet.
**Learned:** fake sync clients must raise `ValueError` on unknown peers
(not assert) now that resolve probes reachability on every foreign header.
**Next:** slice 3 — `quote_flattened` in sync JSON + `PartialFailure` exit.

## 2026-07-23 — Clone quote replies slices 0–1 (Composer)
**Did:** implemented plan slices 0 and 1 for ADR-0036. Slice 0:
`message_to_dict` now emits `quote_text` and, for cross-chat quotes, 
`reply_to` as `{"id", "peer"}` instead of a bare id that resolves against
the wrong chat; CONTRACT + live smoke shape updated. Slice 1:
`clone/replies.py` is a pure classifier
(`mapped-in-leg` / `mapped-cross-leg` / `foreign-peer` / `flatten`, stop on
invalid parent/quote/album/unrecognized header); `reply_from`/`reply_media`
no longer reject; transport consumes the classification and still builds
`InputReplyToMessage` only for mapped-in-leg (cross-leg/foreign flatten until
slice 2). Fixtures for source 2374/2378. Full gate: 680 passed, 8 skipped;
ruff + pyright + architecture + coverage green.
**Decided:** until `clone/quotes.py` lands, foreign-peer and mapped-cross-leg
flatten rather than wedge — progress with recorded loss beats a stuck cursor.
**Learned:** `topics.placement_only` is true for any forum_topic header
without a top id, including on non-forum destinations; reply_flattened must
gate that exception on `destination_kind == "forum"`.
**Next:** slice 2 — `clone/quotes.py` resolver (native quote / rendered
fallback by reachability).

## 2026-07-23 — Close the `mutual-chats --plain` test gap (Claude Opus 4.8)
**Did:** added `test_mutual_chats_plain_output_sanitizes_and_lists_chats` to
`tests/test_cli_mutual_chats.py` — asserts the frozen TSV column order
`id, type, username, display_name` plus tab/newline sanitization and the
empty-username cell. Then mutation-audited every projection in
`read_ops.execute`: swapped each of the 13 `Result(data, …_to_rows(data))`
lines for a mismatched projection and ran the full suite per mutant. All 13
are now killed; `mutual-chats` was the only survivor before the new test
(verified by re-running the mutant with the new test deselected: 674 passed).
`675 passed, 8 skipped`; ruff check/format clean.
**Decided:** nothing architectural — test-only change, no CONTRACT or MAP
impact.
**Learned:** the `mutual-chats` gap was worse than a crash. Swapping
`mutual_chats_to_rows` for `to_rows` does not raise `KeyError: 'peer'`,
because `mutual-chats` data carries both `peer` and `chats` — `--plain`
would have silently printed the resolved user instead of the common chats.
Line-targeted mutation of the projection table is a cheap coverage audit for
this repo (whole suite runs in ~4s, so 13 mutants cost under a minute).
**Next:** none for this thread; the same mutation script generalizes if the
`_SPECS`-style registry refactor of `read_ops` lands later.
## 2026-07-23 — read_ops registry collapses the triple table (Claude Opus 4.8)
**Did:** `from_cli`, `from_batch`, `execute`, and `BATCH_OP_NAMES` each carried
their own copy of the 13-operation list — four edits per new read op, with
nothing failing on drift. Replaced them with a single `_SPECS` table in
`read_ops.py` keyed by op name, one row per operation holding its CLI builder,
batch builder, `fetch` call, and `--plain` `rows` projection; `BATCH_OP_NAMES`
now derives from the table and `execute` pairs `fetch` with `rows`
structurally. Added `tests/test_read_ops.py` (32 tests) asserting the table
covers the `ReadOperation` union exactly and that every op is reachable from a
real CLI invocation and a batch payload. Behavior unchanged; 413 → 382 lines,
ceiling lowered to match in both `scripts/check-architecture.py` and its test.
706 passed / 8 skipped; ruff format + check, pyright, architecture gates green.
**Decided:** clone stays frozen per ADR-0028 — its 874-line hotspot and the
duplicated destination validation in `commit_init`/`sync_text` were left
untouched by owner decision. No ADR needed here: the read seam of ADR-0034 is
unchanged, only its internals.
**Learned:** the first attempt kept 13 per-op `_run_*` functions and came out
*longer* than the original (423 vs the 413 ceiling) — the architecture check
caught a "simplification" that wasn't one. Splitting the spec into `fetch` +
`rows` deleted 11 of those functions, because the repeated
`Result(data, to_rows(data))` pairing was the actual duplication. Second
gotcha: `git checkout <file>` to undo a mutation test wiped the uncommitted
refactor — commit before mutation-testing. Mutation testing also surfaced a
pre-existing hole: swapping `rows=` for `mutual-chats` passes the whole suite
even though it raises `KeyError: 'peer'` live, because
`tg mutual-chats --plain` has no test at all.
**Next:** cover `tg mutual-chats --plain` in `tests/test_cli_mutual_chats.py`,
then sweep the other `_SPECS` rows for the same missing-projection gap.
## 2026-07-23 — CLI entry split + docs archive boundary (Claude Opus 4.8)
**Did:** split `cli.py` (909 lines, 7 functions) into four modules with one job
each — `parser.py` (375, grammar), `preflight.py` (168, pre-session validation
and gates), `dispatch.py` (213, network routing), `cli.py` (213, lifecycle);
turned `scripts/check-architecture.py` budgets from exact-equality baselines
into ceilings and extended its read-ownership check to all four modules;
repointed tests that patched `cli.session`/`cli.media_cmd` at the owning
modules; split `DEVLOG.md` at the 1.0.0 release into `DEVLOG-v1.md` (1,736
lines archived, 624 live); marked `docs/superpowers/` a closed archive in
place. ADR-0035. 674 passed / 8 skipped, ruff + pyright + architecture +
coverage gates green.
**Decided:** split the entry point by concern, not by command — ADR-0034 bars
the entry point from importing read command modules, so per-command grammar in
`commands/*.py` would have broken that seam. Kept `docs/superpowers/` where it
is: renaming would have rewritten 67 links, six of them inside ADRs that are
supposed to be immutable records.
**Learned:** the architecture gate compared line counts with `!=`, and a test
pinned that shrinking the file must fail too. That made `cli.py` frozen rather
than merely large — every edit, including one-liners, needed the budget
constant changed in the same commit, and it would have blocked this refactor
first. Also: the live documentation was never the problem. Of 18,295 doc lines,
only 1,691 are live contract; 66% was completed plans. The ratio that looked
like 2.7:1 against code is 0.25:1 once history is separated from contract.
**Next:** none pending. If `clone` is ever reopened, its 874-line command
module is the next hotspot with the same shape.

## 2026-07-23 — Shared read-operation architecture deepening (Codex)
**Did:** ran the scoped architecture-health survey over `cli.py`, clone
workflows/state, related history, tests, and ADRs; wrote the self-contained
before/after report to the OS temp directory; and selected the active
interactive/batch read seam over a mechanical clone split. Published spec
#23 and tracer tickets #24/#25. Added the closed typed `read_ops` module for
all 13 shared reads, moved batch and interactive dispatch behind it, and added
an exact-baseline/ownership architecture checker to CI. Permanent checker
regressions cover absolute/direct-symbol/relative imports, mixed
`media.manifest` dispatch, and unrecorded baseline shrinkage. Final full gate:
672 passed, 8 skipped; Ruff check/format clean; Pyright 0 errors; architecture
check passed; 23-namespace coverage passed.
**Decided:** ADR-0034. Interactive argparse/process/output and batch
JSONL/sequencing/error envelopes stay separate adapters; coercion and command
selection live in one deep typed module. CONTRACT is unchanged because flags,
JSON, TSV, exits, safety, and Telegram requests are unchanged.
**Learned:** the strongest hotspot was not raw clone line count but duplicated
knowledge across two currently changing adapters; the 2026-07-23 ISO fix was
concrete evidence. Independent Spec + Standards review found ratchet bypasses
through direct/relative imports and the mixed media module; four red
regressions plus the relative-import regression now fail those paths closed.
**Next:** publish the implementation PR, wait for green CI and mergeability,
then merge without running Telegram mutations.

## 2026-07-23 — Engineering skill flow setup (Codex)
**Did:** configured the repository for the Matt Pocock engineering flows:
GitHub Issues as the tracker, canonical triage-label routing, and single-context
domain docs that preserve `docs/decisions/` as the only ADR directory. Added
`docs/agents/`, ADR-0033, AGENTS/CLAUDE routing, and MAP/index entries; created the
four missing GitHub labels (`needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`; `wontfix` already existed). Full gate: 664 passed, 8
skipped; Ruff check/format, Pyright, and 23-namespace coverage passed.
**Decided:** `CONTEXT.md` remains lazy and should appear only when domain
modeling produces durable vocabulary. Pull requests are implementation
artifacts, not an incoming triage surface.
**Learned:** the generic flow defaults use `docs/adr/`, which would duplicate
tgcli's established `docs/decisions/`; repository-local routing must override
that default explicitly.
**Next:** hand off the line-budget evidence to a fresh architecture-health
session and run the scoped deepening survey.

## 2026-07-23 — Retrospective PR fixes and review correction (Codex)
**Did:** audited merged PRs 18, 20, and 21 from the last whole-branch review
point; confirmed PR 19 never merged. Added regression coverage and minimal
fixes for batch blank-line caps and ISO dates, combined bulk-media filters,
typed partial-failure exits, output ownership, auth-revocation propagation,
broadcast export flood waits, unoccupied phone resolution, and atomic
cross-process resolve-phone cooldown reservations. Updated CONTRACT, MAP, and
SKILL. Final gate: 664 passed, 8 skipped; Ruff check/format, Pyright, and the
23-namespace coverage check all passed. Independent final Spec review found no
remaining actionable defect.
**Decided:** withdraw the earlier `mutual-chats` type finding: Telethon's
`GetCommonChatsRequest.resolve()` converts the high-level entity through
`get_input_user`, so no production change is required. Historical PR 18
sequencing/scope issues are recorded as process findings, not rewritten.
**Learned:** whole-diff adversarial review found contract failures that focused
happy-path tests and green aggregate CI missed, especially JSON-to-domain
coercion, concurrent cooldown reservation, and exception mapping inside a
partial-success loop.
**Next:** review the published fix branch and merge it when ready.

## 2026-07-23 — Independent implementation and review gates (Codex)
**Did:** strengthened `AGENTS.md` with public-seam TDD, exact Telethon request
type checks, coherent PR scope, independent whole-diff Spec + Standards review,
adversarial CLI coverage, safe live-smoke boundaries, and the complete uv
verification gate. Replaced the duplicated Cursor rule set with a thin adapter
and explicit implementation/handoff checklist; removed its contradictory
`commit (if asked)` instruction.
**Decided:** green focused tests and CI are necessary but not sufficient;
feature authors do not provide the only final approval of their own work.
**Learned:** permissive fakes and copied agent rules can both hide drift:
external type mismatches in code and workflow contradictions in instructions.
**Next:** finish the active PR #21 regression fixes, then apply the independent
whole-diff gate before merge.

## 2026-07-23 — Post-merge review of PRs 20 and 21 (Codex)
**Did:** reviewed PR #20 (`f7542b6`) and PR #21 (`6096519`) against their
contracts, ADRs, and repository standards. PR #20 was clean; focused API tests
reported 81 passed. PR #21's full gate reported 652 passed, 8 skipped, with
Ruff, format, Pyright, and the 23-namespace coverage gate clean. The review
found four functional contract defects in PR #21: wrong `TypeInputUser`
construction for `mutual-chats`, rejected/ignored combined bulk-media filters,
blank lines counted against the batch op cap, and unparsed batch ISO date
filters. A focused repro confirmed the date-filter `datetime`/`str` TypeError.
**Decided:** treat the findings as follow-up maintenance fixes; this review
changes no production code and makes no new architectural decision.
**Learned:** mocked request handlers and aggregate green gates did not exercise
Telethon's exact request-field type or batch JSON-to-domain coercion.
**Next:** fix PR #21 findings test-first, starting with `mutual-chats` and batch
date parsing.

## 2026-07-23 — Slice 5: read-only tg batch (Composer)
**Did:** ADR-0032 slice 5 — `tg batch` JSONL runner (cap 100, RO allowlist,
any failure → nonzero exit, `--fail-fast`, no doctor). New
`commands/batch.py`; CONTRACT/MAP/SKILL/PROPOSALS updated.
**Decided:** stdin parsed before session open so allowlist/cap fail closed.
**Learned:** none.
**Next:** owner live smoke of archive/mute and a small batch against main.

## 2026-07-23 — Slice 4: bulk media download (Composer)
**Did:** ADR-0032 slice 4 — `media download --message-ids` and filter
`--type/--since/--limit` (cap 100). PartialFailure emits JSON with
`failed[]` and nonzero exit. CONTRACT/SKILL/PROPOSALS/MAP updated.
**Decided:** PartialFailure exception carries result document for stdout.
**Learned:** none.
**Next:** Slice 5 — read-only `tg batch`.

## 2026-07-23 — Slice 3: incremental export messages (Composer)
**Did:** ADR-0032 slice 3 — `export messages --after-id/--append/--resume`.
Append requires a cursor; resume parses last JSONL `id` (fail closed).
CONTRACT/PROPOSALS/DEVLOG updated.
**Decided:** none beyond ADR-0032 grilling.
**Learned:** none.
**Next:** Slice 4 — bulk media download.

## 2026-07-23 — Slice 2: dialog archive and mute (Composer)
**Did:** ADR-0032 slice 2 — `dialog archive|unarchive|mute|unmute`. Mute
requires `--until` or `--forever` (exit 2 otherwise). Same pin-style
gate/audit, no preview. CONTRACT/SKILL/PROPOSALS/MAP updated.
**Decided:** forever = `mute_until = 2**31-1`; unmute = `0`.
**Learned:** none.
**Next:** Slice 3 — incremental export messages.

## 2026-07-23 — Slice 1: tg mutual-chats (Composer)
**Did:** ADR-0032 slice 1 — `tg mutual-chats <user>` over
`messages.GetCommonChatsRequest` (limit 100). Returns `{peer, chats, count}`;
empty chats ok; non-user peer exit 2; missing user exit 4. CONTRACT/SKILL/
PROPOSALS/MAP updated. Tests in `tests/test_cli_mutual_chats.py`.
**Decided:** bots allowed as the mutual peer (Telegram supports getCommonChats
on bots that share groups); groups/channels as the ref stay blocked.
**Learned:** none.
**Next:** Slice 2 — dialog archive/mute.

## 2026-07-23 — ADR-0032 data plumbing scope + plan (Composer)
**Did:** owner-approved PROPOSALS package (mutual-chats, dialog
archive/mute, incremental export, bulk media, RO `tg batch`) grilled and
accepted as ADR-0032; wrote the ADR, index row, MAP decisions line, and
`docs/superpowers/plans/2026-07-23-data-plumbing.md`. Out of scope remains
export bundle / MSG-001 / FEED-001 / ACCOUNTS-001 / moderation verticals.
**Decided:** ADR-0032. One PR with five slice commits; mute requires
`--until` or `--forever`; batch and bulk media hard-capped at 100; batch
exit nonzero on any failed op; doctor not in batch allowlist.
**Learned:** grilling closed the agent footguns before code.
**Next:** Slice 1 — `tg mutual-chats`.

## 2026-07-23 — Cursor onboarding + pyright reply_to_ephemeral (Composer)
**Did:** Cursor first-session track C+A from the orientation plan. Added
always-apply rule `.cursor/rules/tgcli-maintenance.mdc` (maintenance mode,
TDD, docs discipline, how-to-task examples). Mapped it in `docs/MAP.md`.
Fixed frozen pyright baseline in `clone/replies.py` via
`getattr(header, "reply_to_ephemeral", False)` plus a unit test that
rejects ephemeral reply shapes. Verified: `tg doctor --json` ok (3
accounts); `.venv/bin/pytest -q` → 627 passed, 8 skipped; ruff clean;
pyright 0 errors.
**Decided:** gated product work (MSG-001 / FEED-001 / PROPOSALS) stays
owner-triggered after onboarding; no ADR for the getattr hardening.
**Learned:** system `pytest` outside `.venv` fails collection; always use
`.venv/bin/pytest`. Sandbox blocks session locks / invocation journal —
doctor needs unrestricted FS for live auth checks.
**Next:** owner picks one gated item (`tg batch`, albums, `tg changes`,
or `accounts login`) or a live Telegram task.

## 2026-07-23 — Fix tg api bool/scalar RPC serialization (Composer)
**Did:** bug fix for live finding: `tg api account.updateStatus --write`
reached Telegram then crashed on `bool.to_dict()`. Added failing unit test
and `_serialize_rpc_result` so TLObjects still use `to_dict()` while bare
Bool/int/null pass through `_sanitize_result`. CONTRACT §6 notes scalar
`result`. Suite: `81` api-related tests green; ruff + pyright clean.
**Decided:** serialize scalars as JSON values in `result` (additive contract
clarification), not wrap them in a fake TL dict.
**Learned:** many MTProto writes return Bool; the previous envelope assumed
every RPC result was a TLObject.
**Next:** open PR for `claude/fix-api-bool-result` (live recheck on main:
`{"method":"account.updateStatus","result":false}` exit 0).

## 2026-07-23 — Remainder live checks without clone (Composer)
**Did:** finished non-clone live remainder on `main`. PASS: accounts list,
media manifest (+ `--type photo`), info `--full`, search `--all`, message
`--context`, `stories.getPeerStories`, export subscribers `--limit 201` →
exit 2 (ADR-0031), custom_emoji id as decimal string + HTML send with
`<tg-emoji>`, send `--file`+caption+`--silent` then delete, api hard-deny
`auth.logOut` → exit 2, resolvePhone invalid + local cooldown → exit 5.
**Decided:** none (verification).
**Learned:** `tg api account.updateStatus --write` reaches Telegram (Bool
ok) but crashes: `AttributeError: 'bool' object has no attribute 'to_dict'`
in `commands/api.py` `call()` — bool/int RPC results are not handled.
**Next:** owner call — fix bool/scalar sanitize in `tg api` (bug), or stop.

## 2026-07-23 — Extended live checks on main beyond bench (Composer)
**Did:** owner-requested live pass for commands outside smoke/bench on
`main`, mostly via Saved Messages. PASS: doctor, contacts list/search,
resolve `@CrwDdy`, thread (parent+reply then cleanup delete), HTML
send/edit preview→commit, forward me→me, mark-unread/read, dialog
pin/unpin, delete preview→commit. Left visible proof in Saved Messages
(HTML-edited message + its forward). Initial script falsely flagged
`resolve me` (exit 0; shape is `{peer:{id,…}}`, not top-level `id`) —
recheck confirmed PASS.
**Decided:** none (verification only).
**Learned:** `resolve me` returns a `peer` envelope; do not assert bare `id`.
**Next:** none unless owner wants phone-resolve or custom-emoji send.

## 2026-07-23 — Live smoke + bench on main (Composer)
**Did:** ran owner-requested live verification on account `main` (no clone).
`TGCLI_LIVE_SMOKE=1 .venv/bin/pytest tests/live -q` → `8 passed` (~8.5s).
`.venv/bin/python scripts/bench.py --account main` → `13/13 PASS`, 0 skip,
0 fail, ~16.4s total. Bench wrote one Saved Messages ping and exercised
read/search/message/info/count/api/send/media-download/export paths.
**Decided:** none (verification only).
**Learned:** no FloodWait on this run; export-messages and export-subscribers
both PASS (subscribers default `@mir_ivanova`).
**Next:** optional deeper checks (doctor/contacts/edit-delete-forward preview)
if needed; clone stays owner-gated separately.

## 2026-07-23 — PR #18 review fixes: export limit, emoji id string, resolvePhone cooldown, ADR-0031 (Composer)
**Did:** addressed Bugbot/Standards/Spec/thermo-nuclear findings on
`claude/agent-quick-wins`. (1) Broadcast `export subscribers --limit > 200`
now exits 2 instead of full-crawl + arbitrary slice; unlimited omit-`--limit`
keeps prefix-union (ADR-0031). (2) `custom_emoji[].id` emitted as decimal
string. (3) Shared ~3s `resolve_phone` cooldown for `tg resolve +…` and
`tg api contacts.resolvePhone`. Docs: CONTRACT, MAP, SKILL, ADR-0010 note,
ADR-0031 + index.
**Decided:** reject finite broadcast limits above the page size rather than
invent a “most recent N past 200” API Telegram does not offer cheaply.
**Learned:** review consensus across four agents was stronger on export
semantics and JS id precision than on the argparse exit-1 vs plan exit-2 nit.
**Next:** merge PR #18 after gates.

## 2026-07-23 — ADR-0029 slice 3: media manifest + thread (Composer)
**Did:** executed discovery-inbox Tasks 6–7. `tg media manifest CHAT` dry-run
inventory (`--type`/`--since`/`--limit`, no download) in `media.py`. New
`commands/thread.py` + `tg thread CHAT MESSAGE_ID` with ancestor walk
(depth default 20, hard cap 100, cycle-safe) and opt-in `--replies` via
`get_messages(reply_to=…)` when `message.replies` exposes a cheap thread.
FakeClient `get_messages` gains `reply_to` + `replies=` map. CONTRACT, MAP,
SKILL, PROPOSALS updated; ADR-0029 plan fully shipped.
**Decided:** argparse `--type` invalid choice stays exit 1 (repo convention),
not PolicyError exit 2; newest-first `--since` stops at the first older
message rather than scanning past it.
**Learned:** Telethon `get_messages(reply_to=)` is enough for forum/comment
threads without a raw `GetRepliesRequest` in the wrapper.
**Next:** optional PR for `claude/agent-quick-wins`; backlog remains
mutual-chats / bulk media / incremental export / batch / dialog archive-mute.

## 2026-07-23 — ADR-0029 slice 2: mark-unread + dialog pin/unpin (Composer)
**Did:** executed discovery-inbox Tasks 4–5. `tg mark-unread CHAT` via
`MarkDialogUnreadRequest(unread=True)` in `mutate.py`, with the same
`--readonly` / env gating and fail-closed audit as `mark-read`. New
`commands/dialog.py` + `tg dialog pin|unpin CHAT` via
`ToggleDialogPinRequest`. FakeClient accepts both Bool TL requests. Tests in
`test_cli_mutate.py` / `test_cli_dialog.py`. CONTRACT, MAP, SKILL, PROPOSALS
updated.
**Decided:** keep `mark-unread` top-level (mirrors shipped `mark-read`) and
pin/unpin under the `dialog` namespace, per ADR-0029 — not a unified
`dialog mark-unread`.
**Learned:** both TL methods take `InputDialogPeer`, not a bare InputPeer.
**Next:** ADR-0029 slice 3 — `media manifest` then `tg thread`.

## 2026-07-23 — Close WIP: formatting/export/stories docs + export test (Composer)
**Did:** finished the uncommitted 2026-07-22 WIP on `claude/agent-quick-wins`.
Added a reproducing unit test that broadcast `export subscribers` unions
prefix searches past the page cap (FakeClient hooks for
`GetFullChannel`/`GetParticipants`). Wrote ADR-0030 (outgoing `--format` +
`custom_emoji`); expanded ADR-0010 to 40 methods (`stories.*`); updated
MAP, CONTRACT (broadcast subscriber walk), ISSUES MSG-001, FEATURES,
ADR-0028 deferred note. Formatted `export.py` / `test_cli_send.py`.
**Decided:** formatting lands as ADR-0030 (MSG-001 partial), not a silent
CONTRACT-only change; stories stay an ADR-0010 allowlist bump.
**Learned:** existing subscriber tests still hit `iter_participants` because
the fake channel entity has no `broadcast=True` — the aggressive path needs
an explicit broadcast fixture.
**Next:** commit the WIP (one or more commits), then push / open PR for
`claude/agent-quick-wins`; resume ADR-0029 slices 2–3 when ready.

## 2026-07-22 — Full broadcast-subscriber export + stories read allowlist (Claude Opus 4.8)
**Did:** (1) `export subscribers` now returns every member of a broadcast
channel instead of the 200 Telegram caps a single `getParticipants` at. New
`_iter_all_channel_members` (`src/tgcli/commands/export.py`) unions saturating
prefix searches over a latin+digit+cyrillic alphabet, deepening any prefix that
fills a full 200-page, deduping by id, stopping once the reported member total
is reached. Aggressive path triggers only for broadcast channels when no small
`--limit` is set (`limit is None or limit > 200`); megagroups and explicit small
limits keep the plain single-pass `iter_participants`. Verified on
@mir_ivanova: 200 → 280/281 members. (2) Added four read-only `stories.*`
methods (`getPeerStories`, `getStoryViewsList`, `getStoriesArchive`,
`getStoriesByID`) to `READ_METHOD_ALLOWLIST` (`src/tgcli/commands/api.py`) +
the reviewed-list test, so story-viewer analytics work via `tg api`.
**Decided:** raw `GetParticipantsRequest` + **sequential** issue, not
`iter_participants` and not concurrency. `iter_participants` fires a second
request per search purely to compute a `count` we discard (2× the calls);
issuing prefix queries concurrently reliably trips server flood-wait (measured:
8 concurrent = 5.6s dominated by one flood-stalled call, vs 8 sequential =
3.2s @ ~0.4s each). Net: naive full enumeration 3:19 → tuned 1:07.
**Learned:** for **broadcast** channels the 200 cap is hard for both
`ChannelParticipantsRecent` and `ChannelParticipantsSearch('')` — `offset>200`
returns zero rows and `.count` itself reads 200, so pagination can't see past
it. Prefix-substring search is the only escape and its wall-time floor is set
by Telegram's getParticipants flood-limit, not local work. Emoji/CJK-only
display names with no searchable char can leave a member unreachable (got
280 of a reported 281), so early-break on total may not fire — acceptable.
**Next:** consider a `--fast`/`--complete` toggle if the ~1min full sweep is
too slow for interactive use on large channels.

## 2026-07-22 — Fix edit-commit peer + add `edit --format` (Claude Opus 4.8)
**Did:** two changes to the edit surface. (1) Bugfix: `commit_edit` passed the
raw stored `chat` string straight to `tg.edit_message`, so editing a private
channel by numeric `-100…` id died with `Cannot find any entity`. Wrapped it in
`chatref.parse()` to match `commit_forward`/`send.commit`
(`src/tgcli/commands/mutate.py:50`). (2) Feature: new `src/tgcli/formatting.py`
(`render(text, fmt)` → `(clean_text, entities|None)`), a `--format
{plain,md,html}` flag on `tg edit` (default `plain`), threaded through the
preview payload and re-rendered at commit into explicit `formatting_entities`.
`html` reuses Telethon's HTML parser (bold/italic/underline/strike, blockquote +
`expandable`, code/pre, links, `tg-emoji` custom emoji) and subclasses it to add
`<tg-spoiler>` / `<span class="tg-spoiler">`, which Telethon 1.44 does not emit.
Added `tests/test_formatting.py` (10) + 3 CLI tests; updated the edit fakes to
accept the new kwargs. 594 passed. Updated CONTRACT (edit preview row gains
`format`; new `--format` paragraph).
**Decided:** `edit` defaults to `plain` = parse disabled (verbatim TEXT, no
entities), deliberately *not* inheriting the client Markdown default that
`send` documents — surgical edits should be literal unless formatting is asked
for. Formatting is stored as the raw markup + format name in the preview and
re-rendered at commit, keeping the preview payload JSON-safe. Extends ADR-0028.
**Learned:** Telethon 1.44's HTML parser already handles expandable blockquote
and `tg-emoji`, but silently drops spoiler tags (no entity, text kept) — easy to
miss without checking entity types. Telegram entity offsets are UTF-16 code
units, so surrogate-pair emoji (💸) must shift following offsets by 2; the
subclass inherits Telethon's `add_surrogate`/`strip_text` machinery to get this
right (covered by a dedicated test).
**Next:** consider mirroring `--format` onto `send` for parity (send still uses
the client Markdown default), and surfacing entities in `tg message` read output
so formatted posts can be verified without a raw TL call.

## 2026-07-22 — `send --format` parity + custom-emoji harvest (Claude Opus 4.8)
**Did:** (1) mirrored `--format {plain,md,html}` onto `send` (default `md`, so
existing behavior + CONTRACT hold), routing prepare/commit through
`formatting.render` instead of the client's implicit `_parse_message_text`;
`format` is stored in the preview payload and appended to the send preview row.
(2) Added custom-emoji harvesting: `message_to_dict` now emits `custom_emoji`, a
list of `{id, emoji, offset, length}` extracted from `MessageEntityCustomEmoji`,
so ids can be pulled from any readable post (e.g. тень.exe) and reused as
`<tg-emoji emoji-id="ID">` in `--format html`. Glyphs are sliced with Telethon
surrogate helpers (UTF-16). Updated 5 exact-match message fixtures + 3 CONTRACT
JSON examples, added 4 send tests + 1 read test. 598 passed.
**Decided:** `send` keeps `md` as default (composition convenience, documented),
while `edit` stays `plain` (surgical, literal) — deliberate asymmetry. Custom
emoji surfaced on the universal message shape rather than a bespoke command, so
`read`/`search`/`message`/`export` all expose ids uniformly (additive field).
**Learned:** entity offsets index into `Message.message` (raw), not the
`.text` property (which re-renders markup) — slicing `.text` would drift when
markup is present; fakes only carry `.text`, so the helper prefers `.message`
and falls back. Telethon 1.44 `MessageEntityCustomEmoji.document_id` is the same
id `<tg-emoji emoji-id>` consumes, so harvest→reuse round-trips without a map.
**Next:** optionally let `--format html` accept a shorthand for pasting a raw
unicode+id pair, and add an `entities` passthrough for the long tail (underline
mixes, nested quotes) if a real post needs it.

## 2026-07-21 — Slice 1: resolve + contacts commands (Claude Sonnet 5)
**Did:** executed ADR-0029 slice 1 (`fae503f`..`7102c6b`): allowlisted
`contacts.resolvePhone` as a read method (ADR-0010), added `tg resolve REF`
(resolves `@username`, `t.me` link, numeric id, or `+phone` to a single
`{peer:{id,type,username,display_name,is_contact,is_bot}}`), and added
`tg contacts list` / `tg contacts search QUERY [--global]` in the new
`src/tgcli/commands/identity.py`, reusing the `peer_to_dict` mapping. Local
`contacts search` filters the address book in Python (`scope: "local"`);
`--global` calls `contacts.search` capped at 50 results (`scope: "global"`).
Closed out the slice with three doc edits: this entry, an ADR-0010
reconciliation (`contacts.resolvePhone` moved from "reviewed and rejected"
into the allowlist, 35 → 36 methods), and a CONTRACT.md note on the
`--global` 50-result cap.
**Decided:** phone lookup calls `contacts.resolvePhone` only, never
`contacts.importContacts` — the caller must already hold the phone number,
and Telegram returns not-found (exit 4) when the target's privacy settings
block the lookup, so the allowlist entry cannot be used to enumerate numbers.
**Learned:** ADR-0010's own Consequences require an ADR-0010 update whenever
the read allowlist changes; the resolvePhone allowlisting commit shipped
without that follow-up, leaving the ADR self-contradictory (method both
allowlisted in code and listed under "rejected" in the doc) until this
closeout.
**Next:** all four gates green (581 passed, 8 skipped); proceed to slice 2
per the scoped plan.

## 2026-07-21 — ADR-0029: discovery & inbox scope + plan (Claude Opus 4.8)
**Did:** synced stale local main to origin (was 34 behind), vetted the owner's
feature wishlist against the real post-PR-#17 surface, and wrote
docs/PROPOSALS.md (backlog). Then grilled the top-5 quick wins and captured the
decisions as **ADR-0029** + scoped plan
docs/superpowers/plans/2026-07-21-discovery-inbox.md (3 slices, 7 tasks).
Updated decisions/README, MAP.md, ISSUES.md link, PROPOSALS graduation note.
No production code yet — awaiting owner go per ADR-0026.
**Decided (grill outcomes):** one ADR for all 5; inbox mutations
(mark-unread, dialog pin/unpin) run direct like mark-read, no preview;
`resolve` supports +phone via `contacts.resolvePhone` ONLY (added to read
allowlist, never importContacts); `contacts search` local by default, `--global`
opts into contacts.search; `thread` = ancestors always (depth 20, cap 100) +
replies only via getReplies under `--replies`; `media manifest` = dry-run with
--type/--since/--limit; `mark-unread` top-level (mirrors mark-read), pin/unpin
under `tg dialog`.
**Learned:** always `git fetch` + check origin/main before a coverage audit —
the first pass on a stale tree wrongly flagged shipped commands as missing.
resolvePhone is the only new safety-surface change, which is what makes an ADR
required rather than optional.
**Next:** on owner go, execute slice 1 (allowlist resolvePhone → resolve →
contacts) TDD, one task per commit.

## 2026-07-21 — Export @mir_ivanova subscribers to Google Sheets (Codex)
**Did:** performed a live read-only participant export for `@mir_ivanova`,
including Telegram join timestamps, then built and visually verified a
three-column workbook and imported it through `gog` as a native Google Sheet.
Verified 284 exported API-visible users against Telegram's visible counter of
286, 285 populated rows including the header, oldest-to-newest date order, and
the first/last ranges after Google conversion. No production code changed.
**Decided:** report the result as Telegram API-visible maximum rather than exact
counter equality because both the normal and exhaustive search-slice passes
left the same two-user counter gap. Used the channel creation timestamp for the
creator's otherwise-null join date.
**Learned:** `ChannelParticipantsRecent` stopped at 200 for this broadcast
channel; alphabetic search slices recovered 84 more users, while extended
Unicode slices recovered none beyond that. Telegram management and future
subscriber-export work must use `tgcli`; no legacy Telegram project is an
active fallback or dependency.
**Next:** use the Google Sheet as the handoff artifact; if join-date search
slices become recurring work, scope that capability directly in `tgcli` under
the maintenance-mode gate.

## 2026-07-21 — PROPOSALS: vet owner wishlist against real surface (Claude Opus 4.8)
**Did:** local main was 34 commits behind origin (pre-PR-#17); first pass
analysed a stale tree and wrongly flagged shipped commands as missing.
Fast-forwarded to origin/main, re-checked the real surface, and wrote
docs/PROPOSALS.md — only genuinely-new, un-vetted ideas. Cross-linked from
ISSUES.md, added the MAP.md row. No code changed.
**Decided:** nothing approved (ADR-0026). PROPOSALS holds contacts/`resolve`,
`thread`, read-only `batch`, incremental export + bulk media/`manifest`,
`dialog` state, and the deferred verticals (community/moderation, stats,
security). Messaging tail and change feed are NOT here — already tracked as
MSG-001 / FEED-001 (ADR-0028); wanting one is a re-entry, not a new proposal.
**Learned:** verify the checkout is current before auditing coverage — a
stale local main produced a confident but wrong "these commands don't exist".
`tg doctor` already covers the whoami need; `read --after-id/--since/--until`
already provides the interim change-feed polling path.
**Next:** await owner pick; identity layer (`resolve`, `contacts`) is the
cheapest high-value new work if they want to start.

## 2026-07-18 — File-send TOCTOU snapshot closure (Codex)
**Did:** closed the final file-send TOCTOU gap. Preview now derives byte count
and SHA-256 from one open stream. Commit copies one open of the approved source
into a unique temporary snapshot while computing the same fingerprint,
compares both values to the preview, uploads only that snapshot, and removes it
after success or upload/request/confirmation failure. Original-path MIME and
filename metadata are preserved. Added an adversarial upload hook that replaces
the original after validation and cleanup coverage for all outcomes; amended
the canonical ADR-0028 plan Tasks 7/8 and CONTRACT. Final local gates:
`uv run pytest -q` — `570 passed, 8 skipped in 2.70s`; `uv run ruff check .` —
passed; `uv run ruff format --check .` — `86 files already formatted`; `uv run
pyright` — `0 errors, 0 warnings, 0 informations`; `uv run python
scripts/check-coverage.py` — `coverage OK: 23 namespaces`.
**Decided:** the temporary snapshot is implementation hardening inside
ADR-0028's approved send contract, not a new state model or architectural
surface; it needs no dependency, MAP change, or new ADR.
**Learned:** validating a path and then handing the same path to an uploader is
still unsafe because the uploader reopens it. Hashing the exact bytes copied to
an isolated snapshot makes the proof and the uploaded object identical.
**Next:** run the final whole-branch rereview before pushing.

## 2026-07-18 — Agent correspondence final-review fixes (Codex)
**Did:** applied all five whole-branch review fixes without touching clone:
file-send previews now expose and store SHA-256 and commits revalidate the
absolute path, size, and digest before upload; repeated edits treat
`MessageNotModifiedError` as convergence; raw text and media sends preserve
Telethon's default parse mode and entities; dialog filtering classifies
megagroups as groups; and the unread recipe now drains a fixed checkpoint
window before oldest-first processing. Added changed-size, same-size
replacement, text/caption Markdown, ambiguous edit retry, and megagroup versus
broadcast regressions. Final local gates: `uv run pytest -q` — `566 passed, 8
skipped in 2.56s`; `uv run ruff check .` — passed; `uv run ruff format --check .` — `86
files already formatted`; `uv run pyright` — `0 errors, 0 warnings, 0
informations`; `uv run python scripts/check-coverage.py` — `coverage OK: 23
namespaces`.
**Decided:** no new ADR or dependency is needed: these are narrow correctness
fixes inside ADR-0028's approved send, edit, discovery, and agent-recipe
surface. `docs/MAP.md` remains accurate because no module moved or changed
ownership.
**Learned:** switching an idempotent send to raw TL requests also bypasses the
high-level client's default text parsing unless `_parse_message_text(..., ())`
is applied explicitly; file size alone cannot bind a preview to same-size
replacement contents.
**Next:** run the requested whole-branch rereview before pushing the completed
commit.

## 2026-07-18 — Agent correspondence Task 15 docs closure (Codex)
**Did:** updated `SKILL.md` for the complete ADR-0028 correspondence surface:
`edit`, `delete`, `forward`, `mark-read`, and `doctor`, plus recipes for
walking `read --before-id` history, checking unread dialogs with
`read --after-id`, and retrying the same send commit after a network failure.
Re-read `docs/MAP.md` against `src/tgcli/`: `confirm.py`, `commands/mutate.py`,
and `commands/doctor.py` already have accurate rows, so the map needed no
churn. Final local gates: `.venv/bin/pytest -q` — `560 passed, 8 skipped`;
`.venv/bin/ruff check .` — passed; `.venv/bin/ruff format --check .` —
`86 files already formatted`; `uv run pyright` — `0 errors, 0 warnings`.
**Decided:** this task closes documentation and local quality gates only;
the v1.1 label is the ADR-0028 surface name, not a release-version bump.
No new ADR is needed because ADR-0028 already authorizes the commands and
their safety behavior. ADR-0028's rejected and deferred boundaries remain
unchanged, and clone is untouched.
**Learned:** send and forward retry safety depends on reusing the original
preview ID, whose stored `random_id` allows Telegram confirmation without a
duplicate mutation; a new preview is not an equivalent retry.
**Next:** after merge, perform the owner-gated live visual acceptance:
`tg doctor --json`, `tg dialogs --unread-only --json`, and
`tg search --all` against a known string. This live smoke was not run in this
documentation-only task.

## 2026-07-18 — Task 14 doctor review fixes (Codex)
**Did:** made the doctor online probe convert every ordinary exception into
`checks.error` and exit-0 report data; a missing `.session` now reports
`lock_free: false` without creating a lock file. Added regressions for both
cases and documented the short-lived local lock/writability probes.
**Decided:** `doctor` still acquires a lock only for an existing session, and
its local probe cleanup is best-effort; no Telegram mutation is permitted.
**Learned:** an absent session and an available lock are different health
facts, so `lock_free` must not be inferred by probing a nonexistent session.
**Next:** review the corrective commit, then run the owner-gated live smoke.

## 2026-07-18 — Agent correspondence Task 14 doctor health report (Codex)
**Did:** added `tg doctor`, which inspects every configured account (or one
explicit alias) for session presence, lock availability, writable local state,
and Telegram authorization; added JSON/TSV contract documentation and three
CLI tests covering all accounts, account filtering, and a reported auth/config
failure. Focused test result: `3 passed`.
**Decided:** online `ConfigError` is health data, not a CLI error: `doctor`
returns exit 0 after a completed check and callers inspect `ok`. No Telegram
mutation occurs; the state-writability probe is local and temporary.
**Learned:** doctor must invoke `tgcli.session.client` directly, so tests patch
that module path instead of only a `cli` import alias.
**Next:** run the full quality gates and perform the owner-gated live health
smoke after merge.

## 2026-07-18 — Agent correspondence Slice 2 mutation surface complete (Codex)
**Did:** completed Tasks 5–10 of the ADR-0028 plan: `send` now previews and
commits text/files with reply, topic, silent, and stored `random_id` metadata;
retryable previews move `.json` → `.pending` → `.used`; `edit`, `delete`, and
`forward` use preview→commit; and `mark-read` is a gated, audited direct
mutation. Forward commits use the preview's stored source, destination, and
`random_id`, then fail-close unless Telegram confirms the exact message id.
Added the JSON/TSV contracts and TDD coverage for forward confirmation and
mark-read readonly behaviour.
**Decided:** Slice 2 stays within ADR-0028's existing safety model. Forward
does not add reply/topic flags: it uses Telegram's native forward semantics
from the stored source peer to the stored destination peer, so it creates no
reply header. `mark-read` remains preview-free because it is content-free and
idempotent, while still requiring readonly/no-send gates and a pre-dispatch
audit record.
**Learned:** the frozen clone pyright baseline still has the unrelated
`src/tgcli/clone/replies.py:28` missing-stub error for
`MessageReplyHeader.reply_to_ephemeral`; Slice 2 did not touch clone code.
**Next:** merge Slice 2 and perform the owner-gated live visual smoke: file
send with caption and reply, edit, delete, and forward in a private test chat.

## 2026-07-18 — Agent correspondence Slice 1 read surface complete (Codex)
**Did:** completed Tasks 1–4 of the ADR-0028 agent-correspondence plan: added
the shared agent-facing message fields; `read` ID/date/topic filters and page
metadata; `message --context`; and `search --from` / `--since`. Documented
the additive response and filter contracts. The full local test suite reported
`492 passed, 8 skipped`; ruff check and format gates passed.
**Decided:** all Slice 1 changes remain additive to existing JSON and TSV
contracts. Date lower bounds preserve Telethon's newest-first iteration and
stop at the first older message; sender filtering is delegated through
Telethon's `from_user` parameter. No ADR was needed because ADR-0028 already
authorizes this scoped, contract-additive work.
**Learned:** the repository's current pyright baseline has one unrelated error
in frozen clone code, `src/tgcli/clone/replies.py:28`, for the absent
`MessageReplyHeader.reply_to_ephemeral` stub attribute; it was not changed.
**Next:** start Slice 2 only from its first TDD task: preview commit state in
`safety.py`, with its new failure/retry semantics tested before implementation.


## 2026-07-18 — ADR-0028: v1.1 agent correspondence scope + plan (Claude Fable 5)
**Did:** owner-commissioned product review of v1.0 walked through a
structured grilling session; wrote ADR-0028 (scope: richer message JSON,
id/date pagination, full mutation set under preview→commit with
random_id commits, discovery flags, `tg doctor`), the scoped plan
`docs/superpowers/plans/2026-07-18-agent-correspondence.md` (15 tasks,
3 slices), ISSUES additions MSG-001 and FEED-001, ADR index row. Docs
only — no code yet.
**Decided:** ADR-0028. Rejected: `tg spec`, `tg can`, `tg inbox`, keyed
idempotency journal, opaque cursors. Deferred with triggers: MSG-001,
FEED-001; ACCOUNTS-001 keeps its trigger. Clone stays untouched —
`random_id` confirmation is deliberately duplicated into a new
`tgcli/confirm.py` instead of refactoring the frozen clone.
**Learned:** two review claims were already implemented (structured
FLOOD_WAIT with `retry_after` in CONTRACT §2; t.me links accepted as
chat refs everywhere) — verify review claims against code before
planning around them. `consume_preview`'s burn-on-consume design is what
makes network-failure retries unsafe today; the `.pending` state fixes
that without a journal subsystem.
**Next:** owner reviews the plan; then execute slice 1 (tasks 1–4) on a
`claude/agent-correspondence-s1` branch.

## 2026-07-17 — ISSUES: pre-approve accounts login on session loss (Claude Fable 5)
**Did:** added ACCOUNTS-001 to docs/ISSUES.md — `tg accounts login`
(interactive session (re)authorization) as deferred, pre-approved
maintenance work with re-entry trigger "first revoked or lost session";
noted the operational mitigation (back up `~/.config/tgcli/` and
`~/.local/state/tgcli/`).
**Decided:** the one future feature worth pre-approving is the session
recovery path — it is most needed exactly when it is least convenient to
build. Needs its own ADR at implementation time (auth touches the safety
surface).
**Learned:** nothing new.
**Next:** none; fires on trigger.
