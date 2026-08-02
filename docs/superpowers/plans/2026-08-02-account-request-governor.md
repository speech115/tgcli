# Implementation plan — account-wide request governor (ADR-0072, issue #145)

ADR-0072 is accepted and unimplemented. Today the account is protected by
the ADR-0045/ADR-0052 mechanisms it supersedes; those supersessions are
recorded as declared-but-not-yet-effective precisely because this plan has
not run yet.

The spec is #139's accepted test matrix. It is the acceptance suite and is
not renegotiated here — where this plan resolves something the matrix left
open (field names, flag names), it says so explicitly and the matrix row
still governs the behaviour.

## Why phases, and why this order

The matrix has ~50 rows across seven areas plus 28 existing tests in two
files that are superseded wholesale. That is not one change. The order below
is forced by dependency, not preference: nothing can be gated before there is
a ledger to gate on, nothing can be paced before the seam exists to pace at,
and the old mechanisms cannot be retired until the new one covers what they
covered.

Each phase is a mergeable PR with a green gate. **No phase leaves the account
less protected than it found it** — the ADR-0045/0052 mechanisms stay live
until Phase 6 removes them, and the governor runs beside them until then.

## Phase 0 — request-type registry and seam pins

Foundation only; no behaviour change.

- Enumerate which Telethon request type each command issues (#133 §1's
  table). Several commands in that table were never traced to a concrete
  constructor — this phase traces them or records them as untraced, and an
  untraced command is a gate failure, not a silent gap.
- Pin `_call`'s existence and signature (matrix S3): a coroutine function
  taking `sender, request, ordered=`.
- Fail fast at client construction if `_call` is absent or renamed (S4) —
  never silently run ungoverned.

Exit: registry importable, S3/S4 green, no runtime behaviour changed.

## Phase 1 — the ledger

SQLite under the state dir, following ADR-0060's `clone/statedb.py` pattern.

- Per-request-type cooldown rows (deadline, probe-spent flag).
- Per-request-type last-request timestamp (the pacing reservation).
- Peer-breadth window rows (peer, touched-at), individually durable.
- Reads fail open (L3), read-time clamp on absurd deadlines (L4), mirroring
  `clone/flood.py`'s `MAX_COOLDOWN_S`.
- `busy_timeout` set deliberately rather than left at the driver default
  (C3 — #137's inventory found none set today).

Exit: L1–L4 green. Nothing consults the ledger yet.

## Phase 2 — the seam

- `flood_sleep_threshold=0` for **all** clients, not only `mutation_safe`
  (S1); `request_retries` untouched (S2).
- Wrap `_call`: consult the ledger before dispatch, **reserve the pacing
  timestamp before the request leaves** (ADR-0072 decision 3 — start-to-start,
  and the thing this seam most invites getting wrong), handle the outcome
  after.
- A flood arms the per-type cooldown. Refusal is local, exit 5, zero RPCs
  (G1, G2, G9).
- Media downloads and the CDN-redirect path route through the same seam
  (S5, S6) — the reason `__call__` was rejected as the seam.

Exit: G1/G2/G9, S1/S2/S5/S7 green. S6 may land as a documented gap if the
CDN redirect proves unfixturable (the matrix flagged it as such).

## Phase 3 — the probe

- Fires at 50% of a recorded wait having elapsed.
- Marked spent **write-ahead**, before the attempt (C2) — a crash between
  mark and send must not retry.
- Success clears the record; a 420 rewrites the deadline from the server's
  own `retry_after`, never the stale one (G6, G7, G8).

Exit: G6–G8, C2 green.

## Phase 4 — pacing defaults and the breadth budget

- Per-type intervals: history reads 3 s, `get_messages` by id 10 s per 300
  ids, media transfer 3 s per file, dialog enumeration 3 s, mutations and
  metadata none. `resolve`'s existing 3 s becomes an instance of the general
  mechanism without regressing its behaviour (P6).
- Rolling 100-distinct-peers/24 h budget across runs; exhausting it is a
  **normal stop** — exit 0, checkpoint intact, resume pointer reported (P7,
  P8).
- P9 is the incident's own regression: `--limit 1000` must now pace, and the
  test must assert the governor's interval store is what fires, not
  Telethon's `wait_time`.

Exit: P1–P9, C1, C5 green.

## Phase 5 — deadline reconciliation

- `--timeout` becomes a hang detector; governed sleep does not count against
  it (D1).
- Both exemption mechanisms deleted — `_default_timeout()`'s command
  special-casing and `_long_running()`/`_deadline()` (D2). The replacement is
  no list at all.
- Long commands carry an explicit wall-clock cap; exhausting it is a normal
  stop, exit 0 (D3, D4).
- `SHORT_WAIT`/`WAIT_BUDGET` retired; sleep-vs-exit is decided by the
  remaining cap, and the exit-5 case sleeps not at all (D5).

Flag name for the cap is undecided in the matrix. This plan proposes
`--max-runtime <seconds>`; the matrix row governs the behaviour either way.

Exit: D1–D6 green; `tests/test_clone_cooldown.py` and the `WaitBudget`
constructions in `tests/test_clone_media_cache.py` rewritten or removed.

## Phase 6 — journal, doctor, scheduled wake; retire the old mechanisms

- Journal gains `retry_after`, request type, provenance (`server` |
  `account_cooldown` | `resolve_phone_cooldown`), stop reason, governed
  sleep, request count (L6–L11). **These literal key names are decided in
  this phase**, per the matrix's own note that they are placeholders.
- `doctor` surfaces an active cooldown and stays the only exemption (G4, L5).
- A scheduled pass waking into a partial cooldown exits **0**, doing what
  free types allow and reporting the rest deferred (L12) — the contract
  break.
- Alert fires once at arming, not on every wake (L13). The delivery
  mechanism is undecided in the matrix; this phase decides it and says so.
- Retire ADR-0045 decision 1's storage and ADR-0052 decisions 1–5; flip both
  index rows and both ADR headers from "in force until the governor slice
  lands" to plain superseded.

Exit: L5–L13, G3, G4, G5 green; `tests/test_clone_flood.py` rewritten
against the new store.

## Phase 7 — contract, release, guides

- `docs/CONTRACT.md`: the six edits drafted verbatim on #141, with the
  placeholder field names replaced by what Phase 6 actually shipped.
- CHANGELOG entry and version bump. The exit-5 → exit-0 move is a contract
  break; ADR-0038 rule 2 argues major (`2.0.0`), and per ADR-0058 rule 1 the
  integrator assigns it at merge.
- Guides and `SKILL.md`: what an operator does when the account is cooling,
  that some commands still work under a partial cooldown, and that `doctor`
  surfaces it directly.
- Devlog entry.

Exit: gate green, #145 closable.

## Standing risks

- **The seam is private Telethon API.** S4's fail-fast is the whole safety
  argument; if it regresses, tgcli runs ungoverned and looks fine.
- **The numbers carry one live demonstration.** #140's canary exercised
  end-to-start pacing at ~4.8 s, not the mandated 3 s start-to-start, and
  touched 15% of the breadth budget. Phase 4 is implementing numbers that
  are reasoned, not measured.
- **Both ADR assumptions remain open.** Nothing in this plan settles whether
  a request during a penalty extends it, or whether the penalty keys on rate
  or breadth.
