# ADR-0045: Clone flood containment — account cooldown, --no-comments, preview risk hints

Date: 2026-07-24
Status: accepted

## Context

The 2026-07-24 live sessions produced the project's worst FLOOD_WAIT day:
three peers created within ~45 minutes (channel clone, second channel
clone, its discussion group), a ~13-minute wait on the discussion-link
step, and an agent retry loop that re-armed the limit for over an hour.
The avatar idempotence bug that amplified it is fixed separately (1.2.2).

Research into the deleted mirror implementation (recovered from git,
DEVLOG 2026-07-24) explains why the mirror era "almost never flooded":

1. Mirror performed radically fewer mutations — one peer per source ever,
   no avatar/about copy, no discussion peer.
2. Telethon settings did not change (`flood_sleep_threshold=0`,
   `request_retries=0` in both eras) — that is not the regression.
3. Mirror's FloodWait cooldown was **account-scoped** and enforced locally
   before every mutating command against any source. Clone's
   `retry_not_before` lives inside one clone's state, so a flood caught
   while cloning source A does not stop the very next `init` against
   source B from firing into the already-throttled account. This guard was
   dropped wholesale in the mirror→clone rewrite (ADR-0017) and is the
   real regression.

Telegram's peer-creation limit itself is server-side and cannot be
engineered away; the goal is to flood rarely, cheaply, and only once.

## Decision

1. **Account-scoped clone cooldown.** A FloodWait raised by any mutating
   clone RPC arms, in addition to the existing per-clone
   `retry_not_before`, a persistent account-scoped cooldown record.
   `clone init --commit` and `clone sync` enforce it locally (no network
   call) before starting, for **every** clone of that account, exiting 5
   with `retry_after` exactly like the per-clone gate (CONTRACT §4
   unchanged). Read-only clone commands (`list`, `status`, init preview)
   are never blocked. The ADR-0024 roster exception stands: a roster
   FloodWait defers the roster and arms nothing.
   - Storage: one JSON record per account under
     `TGCLI_STATE_DIR/clones/`, keyed by `account_user_id`, written via
     `tgcli/atomic.py` (ADR-0043 seam). Mirror precedent:
     `mirror/store.py` `record_cooldown`/`cooldown_deadline`.
2. **`clone init --no-comments`.** Opt out of the discussion-group leg at
   preview time: the preview records the choice, commit creates a
   posts-only clone and the state records `comments: "disabled"` (new
   additive value beside `enabled`/`unavailable`/`none`). `sync` skips the
   comment phase and the discussion roster. Re-running init with
   `--no-comments` on a clone whose state already has
   `comments: "enabled"` is a `PolicyError` — the flag cannot orphan an
   existing linked group.
3. **Preview flood-risk hints.** The init preview JSON gains additive
   fields: `peers_to_create` (how many `CreateChannelRequest` calls this
   commit implies: 0 when the destination is already recorded, 1, or 2
   with a linked discussion) and `account_flood`
   (`{"cooldown_until": ISO|null, "last_peer_created_at": ISO|null}` from
   the account record; peer creations are timestamped into the same
   record). Hints are data, not policy — the agent or owner decides
   whether to commit now or wait. Plain output is unchanged.

## Consequences

- A flood caught on one clone pauses **all** clone mutations of that
  account until the deadline — deliberate: Telegram throttles the
  account, and continuing from a different clone reads as hammering and
  escalates the wait ladder (observed live in ADR-0023 and today).
- Two cooldown scopes coexist: the per-clone one (kept for state locality
  and existing tests) and the account one; either alone blocks.
- `comments: "disabled"` is additive in state and JSON; old state files
  load unchanged. CONTRACT gains the value, the flag, and the preview
  fields in the same commit as the code.
- No daemons, no background timers — the cooldown is enforced lazily at
  the next invocation, same as today's per-clone gate.
- The peer-creation limit itself remains: with everything above, a
  worst-case init still hits at most one honest FLOOD_WAIT and completes
  after one wait, instead of escalating.
- Operational discipline stays documented in the tgcli skill: at most
  ~one peer-creating init per day, and exit 5 means wait it out, never
  retry in a loop.
