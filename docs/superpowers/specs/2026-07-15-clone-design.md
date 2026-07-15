# Clone — Lean Channel Copy (supersedes mirror)

Date: 2026-07-15
Status: approved (2026-07-15), implementation in progress
Supersedes: `commands/mirror.py`, `mirror/store.py`, mirror sections of CONTRACT.md §11,
specs `2026-07-13-lean-mirror-init-sync.md`, `2026-07-14-*`, `2026-07-15-protected-reupload-transport.md`.

## Problem

The mirror feature works (live-proven on open and protected sources: text, media,
albums, replies, idempotent reruns) but its implementation is disproportionate:
1,793 production lines across two files — the size of the entire rest of the core
(1,729 lines) — plus 3,650 lines of mocked tests, for a CLI surface of two
subcommands. Root causes, in order of weight:

1. **Parallel infrastructure.** Mirror built its own confirmation flow
   (`--retry-create --confirm <mirror_id>`) instead of `safety.py` preview→commit;
   its own per-account mutation lock under `mirrors/locks/` even though
   `session.client` already holds an exclusive per-account flock for the whole
   invocation (session.py:40-48); its own cooldown store with atomic
   compare-and-max writes.
2. **Zero-duplicate crash guarantee.** random_id bookkeeping, prepare/confirm
   two-phase batches, exact confirmation matching of send envelopes — ~500 lines
   protecting an owner-operated CLI against a duplicate that a human can see and
   delete in seconds.
3. **Migrations for its own development history.** ~250 lines of SQLite schema
   migration handling legacy shapes of tables the feature itself created weeks ago,
   with zero external users.
4. **No read window.** A complex state machine shipped without a `status` command.

The rewrite is named **clone**: it copies a channel on demand (`git clone`
semantics); "mirror" over-promised continuous synchronization.

## Goals

- **Order fidelity is a hard requirement.** Destination messages appear in the
  exact same sequence as the source. Achieved by copying strictly oldest→newest
  (`reverse=True`), so Telegram assigns destination ids in source order; albums
  keep their grouping and position; skipped messages (service / unsupported)
  leave a gap but never reorder anything else. Original post *dates* are not
  preserved — copies carry the clone-time date (Telegram sets a new message's
  date to send time); only the sequence is guaranteed. v1 clones broadcast
  channels only (not groups, forums, or comment threads).
- Full behavior parity with what is live-proven today: text, media, albums,
  replies, protected sources (download/reupload transport), service-message skip,
  idempotent re-runs, FloodWait cooldown, fail-closed on corrupted state.
  One deliberate deviation from mirror: unsupported message kinds are skipped
  and reported, not fatal (see Sync algorithm step 5) — mirror's fail-closed
  allowlist made any channel with a poll mid-history permanently unsyncable
  (this is why the 07-13 showcase sources could not be reused).
- Production code budget: `commands/clone.py` ≤ 400 lines, `clone/state.py` ≤ 150
  lines. Mocked tests ≤ ~1,200 lines total. Exceeding a budget requires cutting
  before adding.
- `clone status` exists from day one.
- Mirror is deleted entirely in the same change, after live acceptance.

## Non-goals

- Comments/discussion mirroring, watch mode, multi-account orchestration.
- Zero-duplicate guarantee under hard crash (see Crash model).
- Migration of existing mirror SQLite state (demo pairs are re-inited as clones;
  demo destination channels are retained per ADR-0015).

## CLI surface

```
tg clone status [SOURCE]                 # read-only; no SOURCE = list all clones
tg clone init SOURCE                     # preview: plan, no mutation
tg clone init SOURCE --commit PREVIEW_ID # create destination channel
tg clone sync SOURCE [--limit N]         # idempotent catch-up copy
```

- `init` preview resolves the source (must be a broadcast channel, not megagroup),
  reports title, approximate message count, protected flag, and returns a
  `preview_id` via `safety.create_preview` — the same TTL'd single-use mechanism
  `send` uses. All custom confirmation flags (`--retry-create --confirm`) are gone.
- `init --commit` and `sync` run with `session.client(mutation_safe=True)`,
  respect `--readonly`/`TGCLI_READONLY`/`TGCLI_NO_SEND` (checked before
  config/session, as in `send`), and append to the shared fail-closed audit log.
- `sync` has no default `--timeout` (backfill can run long); everything else
  keeps the global 60 s default.
- `sync --limit N` copies at most N batches per run and reports
  `"more": true` when the head was not reached — controlled portions for large
  backfills and predictable run time for agents. Re-running continues from the
  cursor; idempotence is unaffected.
- Output follows the core contract: command functions return a plain dict +
  `to_rows(data)`; only `cli.py` emits. Exit codes reuse the existing table
  (2 policy, 3 config/busy, 4 not found, 5 flood-wait).

## State

One JSON file per clone: `$TGCLI_STATE_DIR/clones/<clone_id>.json`, where
`clone_id = sha256(account_user_id:source_peer_id)`. Written atomically
(tempfile + fsync + `os.replace`), mode 0600. Synthetic example:

```json
{
  "version": 1,
  "account_user_id": 100000001,
  "source_peer_id": 1234567890,
  "source_title": "Example Channel",
  "destination_peer_id": 1987654321,
  "creation_marker": "tgcli-clone-<clone_id-prefix>",
  "cursor": 42,
  "id_map": {"7": 3, "8": 4, "12": 5},
  "retry_not_before": null,
  "created_at": "2026-07-15T12:00:00+00:00",
  "last_synced_at": "2026-07-15T12:34:56+00:00"
}
```

- `cursor` — highest source message id fully processed (copied or skipped).
- `id_map` — source id → destination id; needed to map replies. Kept whole;
  a 10k-message channel is ~200 KB of JSON, acceptable for the target use.
- `retry_not_before` — FloodWait cooldown, one field instead of a separate
  per-account cooldown store. Checked before any mutation; violating it is a
  `PolicyError` with the deadline in the payload.
- Corrupted file or unknown `version` → `PolicyError` with a clear message,
  never a raw traceback. **No migrations**: version bump means the file is
  rejected and the user re-inits (or a one-off script converts, if ever needed).

No SQLite. No locks of clone's own — `session.client`'s per-account flock
already serializes tg processes per account.

## Init: destination creation and recovery

The only ambiguous-crash window worth handling is between `channels.createChannel`
and recording `destination_peer_id`. Handling (kept from mirror, simplified to
one mechanism, ~40 lines):

1. `--commit`: write state with `destination_peer_id: null` and a unique
   `creation_marker`; create the channel titled with the marker; record
   `destination_peer_id`; rename the channel to the real title.
2. On any later run with `destination_peer_id: null`: scan owned private
   broadcast channels for the marker title. Exactly one match → adopt it and
   continue. None → create again. Multiple → `PolicyError`, manual resolution.

No `creation_state` enum, no `retention_class` column, no blocked/dispatched
bookkeeping — the null/non-null destination plus the marker covers all of it.
Destinations are user-owned and never auto-deleted (ADR-0015 carries over).

## Sync algorithm

1. Load state; enforce cooldown; resolve source and destination entities.
2. **Tail verification** (the crash model, ~30 lines): fetch the destination's
   last message id. The baseline is the largest known destination id in
   `id_map`, or 1 for a fresh clone (the destination's own creation service
   message, which clone never sent). If the destination's last id exceeds the
   baseline, a previous run crashed after sending but before saving state.
   Report the mismatch (count of unexpected messages) as a `PolicyError` with
   instructions; the user deletes the extras or re-points. v1 does not
   auto-adopt.
3. Iterate source history `min_id=cursor, reverse=True`, buffering contiguous
   album batches by `grouped_id is not None` (never truthiness — live gotcha).
4. Skip service messages (`action is not None`), count as `skipped_service`
   (live gotcha: every fresh channel starts with one).
5. Validate each batch against the supported-kind allowlist (same set as mirror
   today). Unsupported kinds are **skipped and reported**, never fatal: the sync
   report lists them as `skipped_unsupported: [{"id": 57, "kind": "poll"}, ...]`
   and the cursor advances past them. Nothing is skipped silently.
6. Choose transport per batch (ADR-0016 rule, verbatim):
   - **reupload** if the source or any message is protected (`noforwards`) OR
     the batch contains a reply (Telegram silently drops `reply_to` on
     `ForwardMessagesRequest` — live gotcha);
   - **native forward** otherwise.
   Both transport implementations are transplanted from `mirror.py`
   (~200 lines): forward path, and download-to-tempdir → `uploadMedia` →
   `sendMedia`/`sendMultiMedia` reupload path with reply mapping via `id_map`.
7. After each batch is confirmed by the send response, update `id_map` and
   `cursor`, write state atomically, continue.
8. `FloodWaitError` → persist `retry_not_before` in state, exit 5 with
   `retry_after` (Telethon retries/sleeps disabled via `mutation_safe=True`).

### Crash model (accepted trade-off)

State is saved after each confirmed batch. A hard crash between send and save
duplicates at most one batch on the next run — detected by tail verification
and fixed manually. This replaces the entire random_id / prepare-confirm /
exact-envelope-matching layer (~500 lines) with ~30 lines. Accepted explicitly
by the user on 2026-07-15.

## Module layout

```
src/tgcli/commands/clone.py   # ≤400: preview/commit/sync/status orchestration,
                              # batch validation, both transports
src/tgcli/clone/state.py      # ≤150: load/save/validate state file, cooldown field
```

Reused from core: `chatref.parse`, `session.client(mutation_safe=...)`,
`safety.enforce_mutation_allowed` / `create_preview` / `consume_preview` /
`append_audit`, `errors.py` types, `output.py` emitters, invocation journal
(automatic). Nothing new is added to core.

Deleted with mirror: `commands/mirror.py`, `src/tgcli/mirror/`,
`tests/test_cli_mirror_*.py`, `tests/test_mirror_store.py`, the mirror
subparser block in `cli.py`, CONTRACT.md mirror sections.
Kept: `mirror_probe.py` + its script/tests (independent read-only diagnostic),
`scripts/seed_demo_channel.py` (live fixture seeder).

## Testing

- Mocked: extend the existing `FakeClient` in conftest style; black-box
  `test_cli_clone_init.py` / `test_cli_clone_sync.py` / `test_cli_clone_status.py`
  driving `main([...])`, plus `test_clone_state.py` for the state file.
  Budget ≤ ~1,200 lines total.
- **Regression tests for every live gotcha**, named as such: service-message
  skip, `grouped_id=0` album, reply forces reupload transport, corrupted state
  → PolicyError, cooldown persisted before exit 5, tail mismatch detection,
  unsupported kind skipped with report entry (not fatal).
- **Live acceptance is the release gate**, not test count: re-run the Stage-2
  demo (open pair with reply+album, protected pair) via `tg clone`, verify
  visually that the destination message sequence matches the source one-for-one
  (order fidelity), and re-run sync to confirm idempotence (0 copied). Mirror is
  deleted only after this passes.

## Documentation changes

- CONTRACT.md §11 rewritten for `tg clone` (JSON/TSV shapes, exit codes,
  cooldown/timeout semantics).
- New ADR-0017: clone supersedes mirror; records the tail-verification crash
  model, the no-migration state policy, and the complexity budgets.
- MAP.md, PLAN.md, DEVLOG.md updated in the same change.

## Implementation order

TDD throughout: failing test first, then minimal code. Each task is independently
verifiable and leaves the suite green. Written self-contained so Codex can take
over any task from its description alone.

1. ✅ **`clone/state.py`** — state file load/save/validate + cooldown field.
   Pure, no network. Acceptance: `test_clone_state.py` covers round-trip,
   atomic write, unknown `version` → PolicyError, corrupted file → PolicyError,
   cooldown read/write.
2. ✅ **`clone status`** — read-only; lists clones from state files (no SOURCE) or
   one clone's progress (with SOURCE). Wire into `cli.py`. Ships the read window
   first. Acceptance: JSON/TSV shape, empty-state case, `test_cli_clone_status.py`.
3. ✅ **`clone init`** — preview via `safety.create_preview` (no mutation); commit
   creates the destination channel, records it, marker-based recovery on a
   half-created channel. Acceptance: preview does not touch network mutation,
   commit path, recovery adopts/creates/blocks correctly, `test_cli_clone_init.py`.
4. ✅ **`clone sync` — text** — cursor iteration oldest→newest, service skip,
   unsupported skip+report, native forward for plain text, tail verification,
   state saved per batch, FloodWait → cooldown + exit 5, `--limit N` + `"more"`.
   Acceptance: order preserved, idempotent rerun, skip counters,
   `test_cli_clone_sync.py`.
5. ✅ **`clone sync` — media + albums** — extend batches to media; album grouping
   by `grouped_id is not None`. Acceptance: album stays one unit, position kept.
6. ✅ **`clone sync` — replies + protected reupload** — transplant both transports
   from `mirror.py`; reply-bearing or protected batch → reupload; reply mapped
   via `id_map`. Acceptance: reply lands on the right parent, protected source
   copied.
7. **Docs** — rewrite CONTRACT.md §11 for `tg clone`; update MAP.md/PLAN.md in
   the same commit as the code they describe.
8. **Live acceptance** — re-run the Stage-2 demo pairs via `tg clone`, verify
   order fidelity visually, confirm idempotent rerun (0 copied).
9. **Delete mirror** — remove `commands/mirror.py`, `mirror/`, mirror tests, the
   mirror subparser in `cli.py`, and mirror CONTRACT sections. Only after task 8.

## Decisions log (2026-07-15, resolved with the user)

- Rewrite path: clean rewrite with transplant; scope v1 = full live-proven
  parity; crash model = tail verification.
- Unsupported kinds: skip + report (deliberate deviation from mirror).
- `sync --limit N` is in v1.
- Repo hygiene: demo branch merges to main first; lab branches
  (`claude/mirror-r1-controlled-lab`, `codex/mirror-aggregate-checkpoints`)
  are preserved as `archive/*` tags and the branches deleted; fully merged
  mirror branches deleted outright. Clone work happens on `feature/clone`
  with a PR to main. Mirror code stays in tree (frozen) as transplant donor
  and is deleted in the final task after live acceptance.
- Executor: Claude implements; the plan is written self-contained so Codex
  can take over any task.

## Open questions

None.
