# Governor phase 6 — journal, doctor, scheduled wake; retire the old guards

Part of #145. Prerequisite: phase 5. Spec: ADR-0072 decision 4, #139 matrix
rows **G3, G4, G5, L5–L13**.

Two jobs: make the governor's decisions visible, and remove the mechanisms it
replaces. Until this phase the old ADR-0045/0052 guards run alongside the new
one; after it they are gone and the governor is the only protection.

## The visibility problem being fixed

When the 21.5-hour ban happened, the figure had to be reconstructed by
subtracting timestamps out of a state file. The journal recorded that a
command exited 5 and nothing about why or for how long. An hourly scheduled
pass under an 18-hour cooldown exited 5 eighteen times and alerted nobody.

## Files

| File | Change |
|---|---|
| `src/tgcli/invocations.py` | new fields |
| `src/tgcli/commands/doctor.py` | cooldown check + degraded-ledger check |
| `src/tgcli/commands/archive_refresh.py` | partial-cooldown wake → exit 0 |
| `src/tgcli/clone/flood.py` | delete (whole module) |
| `src/tgcli/clone/cooldown.py` | delete the account-record paths |
| `src/tgcli/commands/{archive,clone}.py` | drop `cooled_account` / `enforce_account` calls |
| `docs/decisions/README.md`, `ADR-0045…md`, `ADR-0052…md` | flip "in force until the governor slice lands" to plain superseded |
| `tests/test_clone_flood.py` | rewritten against the ledger (13 tests) |

## Journal fields — decide the literal names here

#139 marked these as placeholders. This phase fixes them; phase 7 writes the
chosen names into `docs/CONTRACT.md` §9. Proposed:

| Field | When present |
|---|---|
| `retry_after` | any flood-related exit |
| `request_type` | the governed key, e.g. `messages.GetHistoryRequest` |
| `provenance` | `server` \| `account_cooldown` \| `resolve_phone_cooldown` |
| `stop_reason` | `breadth_budget_exhausted` \| `wall_clock_cap` \| `cooldown_deferred` |
| `governed_sleep_ms` | any run that paced |
| `request_count` | any run that issued requests |

Keep the journal's existing rule: no message text, no chat refs, no raw API
parameters. A request *type* is not a chat reference.

## Behaviour to implement

1. **`doctor` reports active cooldowns per request type with deadlines, and
   reports a degraded ledger.** `doctor` stays the only command exempt from the
   gate — it must work precisely when everything else is refusing.
2. **A scheduled pass waking into a partial cooldown exits 0.** It does what
   the free request types allow and reports the rest as deferred. **This is the
   contract break** — the same trigger used to exit 5. Anything polling exit 5
   as "needs to wait" changes meaning here.
3. **Alert once at arming, not on every wake.** The second and later wakes into
   an already-armed cooldown are silent-but-successful. #139 left the delivery
   mechanism undecided; decide it here (a journal flag plus one stderr line is
   sufficient — do not add a notification daemon, see the no-daemons rule).

## Traps

- **`tg api` must stay gated** (G5). It is the raw `client(request)` path; the
  gate has to see the request type before `_call` sends it.
- **Deleting `clone/flood.py` removes `MAX_COOLDOWN_S`** — the ledger has its
  own copy, deliberately identical while both were live. Check the constant did
  not drift before deleting the original.
- **The archive/clone `cooled_account` calls did a `get_me` fallback.** The
  governor reads `_self_id` with no RPC. Removing the fallback is fine, but
  confirm no command depended on `cooled_account` returning the `me` object.
- **G3 is parametrized over ~30 commands.** Several were never traced to a
  concrete Telethon constructor. An untraced command is a gap to record, not to
  skip silently.

## Tests

| Row | Test |
|---|---|
| G3 | each command refuses on the exact type it issues, locally, before any network call |
| G4 | `doctor` exits 0 and reports the cooldown with every type armed |
| G5 | `tg api` refuses locally on a gated type |
| L5 | `doctor --json` carries the cooldown check |
| L6–L8 | journal carries `retry_after`, `request_type`, and three distinguishable `provenance` values |
| L9–L10 | four stop outcomes distinguishable in both JSON and journal, no value reused |
| L11 | `governed_sleep_ms` and `request_count` match what the run did |
| L12 | scheduled pass with one type cooled → **exit 0**, JSON says what ran and what deferred |
| L13 | two consecutive wakes into the same cooldown → only the first alerts |

## Done when

- `git grep -n "clone/flood\|cooled_account\|enforce_account"` returns nothing
  outside history.
- Both superseded ADRs' headers and index rows read plain `superseded`, and
  gate check 11 still passes.
- `./scripts/gate.sh` green.
