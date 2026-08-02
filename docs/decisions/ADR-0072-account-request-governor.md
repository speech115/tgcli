# ADR-0072: Account-wide Telegram request governor

Date: 2026-08-01
Status: **accepted** (2026-08-02), **implemented** (2026-08-02, #145's
phases 0–7). This ADR records the decisions reached
across map #131 and tickets #132–#139. It was drafted `proposed` and held
there until #140's owner-gated live canary ran on 2026-08-02; the evidence
is folded in under "Evidence from #140" below, which also records what the
canary did *not* establish. The governor described here is implemented;
`docs/CONTRACT.md` changes shipped with that behaviour.
Supersedes: [ADR-0045](ADR-0045-clone-flood-containment.md) decision 1
only (account-scoped cooldown storage and clone-only enforcement); ADR-0045
decisions 2–3 (`--no-comments`, preview flood hints) stay in force, with
decision 3's `account_flood` preview field renarrowed — see Consequences.
Supersedes [ADR-0052](ADR-0052-clone-short-flood-wait-and-media-reuse.md)
decisions 1–5 (the `SHORT_WAIT`/`WAIT_BUDGET` foreground-retry mechanism)
entirely; decisions 6–7 (the reupload media cache) are untouched.

## Context

On 2026-07-31 a private-dialog `archive backfill` run received a
`FLOOD_WAIT` of **77,586 seconds** (about 21.5 hours) partway through a
sweep of the account's 791 standing 1:1 dialogs, run with
`--limit 1000 --max-dialogs 100`. Nothing in the tool recorded the wait
directly: `invocations.jsonl` carries no `retry_after` field, so the figure
had to be reconstructed by subtracting from `clones/account-<id>.json`'s
`cooldown_until` against the incident's own timeline. Two earlier `archive`
invocations that afternoon had already died to the default 60-second
deadline before the flood, and an interactive `send` about 2.5 hours into
the cooldown also took a genuine server 420 — evidence, not proof, that the
penalty was not confined to the RPC family that earned it (established in
#134's forensic comment).

The structural finding, not just the incident: **the run was wholly
unpaced.** `archive/backfill.py` calls `tg.iter_messages(entity,
limit=limit, offset_id=offset_id)` with no `wait_time` argument — a grep
over the whole tree confirms `wait_time` is never passed anywhere in
tgcli. Telethon's own `_MessagesIter` only arms its inter-page throttle
when the caller's `limit` exceeds 3000
(`telethon/client/messages.py:165-166`); the incident ran at `--limit
1000`, so the one pacing mechanism available in the library never
engaged. The gap was not a missing tuning value. It was a capability the
project never invoked.

This map (#131) planned the fix; it does not build it. #132 (research),
#133 (gate membership), #134 (seam), #135 (pacing defaults), #136 (ledger),
#137 (concurrency), and #138 (deadline reconciliation) each closed with an
owner-grilled resolution; #139 accepted the test matrix that specifies the
implementation slice. This ADR records those six decisions as one design,
the two assumptions none of them could verify, and the contract
consequences. #140 is a proposed live canary, awaiting owner approval, that
would gather the first real evidence for or against the assumptions below;
it has not run.

## Decision

### 1. The cooldown keys on the Telegram request type, not a hand-drawn class, and never on the peer (#133)

A `FLOOD_WAIT` arms an **independent** cooldown per Telegram request-type
constructor (e.g. `GetHistoryRequest`), keyed exactly the way Telethon's own
in-process `_flood_waited_requests` dict already keys it
(`telethon/client/users.py:47-57`). A penalty on one type says nothing
about any other: reading history does not gate sending a message. The key
deliberately **excludes the peer**, even though the official 420 wording is
"method + input parameters + account" and peer is technically an input
parameter of `GetHistoryRequest`. Every command passes the gate on the
request type it is about to issue; `doctor` is the only exemption, so
diagnosing a cooldown is never blocked by the cooldown being diagnosed.
`api` is explicitly not exempt — a refusal is more honest than letting the
command most able to deepen a penalty attempt one.

*Why per-type, not a hand-drawn taxonomy of five request classes* (history
reads, dialog enumeration, mutations, media transfer, metadata lookups —
the shape #134's preparatory brief proposed). The taxonomy already exists
inside Telethon and dies with the process; this decision persists what is
already correct rather than inventing a boundary list to keep in sync. A
newly added command is covered whether or not its author remembered to
classify it — the five-class taxonomy survives only as a candidate shape
for *pacing* (decision 3), a different mechanism.

*Why exclude the peer, accepting more false refusals.* Including the peer
looks closer to the spec and would cut false refusals, but it fails on the
scenario the map exists for: the incident's penalty was earned across 791
distinct peers. Keyed per peer, it would be recorded against whichever
peer happened to be last, and the next bulk sweep would walk the other 790
believing them clean — a gate that reliably permits the exact case it was
built to stop. The accepted cost is a real one: a penalty recorded on
`getHistory` refuses a read of an unrelated chat Telegram might have
served.

*Why not a shared account-wide gate instead of per-type.* One observation
argues for it — the `send` 420 roughly 2.5 hours into the incident cooldown
— but the specification (#123, cited in #132) is explicit that the limit
keys on method, and one observation does not outweigh it. The innocent
reading (the account was independently hot on `send`'s own method too)
fits the evidence equally well. The concern that an operator should *know*
the account is generally hot is real, but it is the ledger's job
(decision 4) to surface that, not the gate's job to duplicate it by being
coarser than the spec.

*The escape from a wrong record: a self-verifying probe, not a bypass
flag.* A cooldown record is an inference from one observed 420, not a
fact — Telegram may have lifted it early, the host clock may have moved,
or the penalty may cover input parameters this run does not use. With no
escape, a wrong record wedges every affected command until its deadline.
The rejected fix was an `--ignore-cooldown` flag: convenient, but it turns
a correctness problem into an operator judgment call made under pressure,
exactly the kind of button ADR-0045's clamp already worries about being
pressed carelessly. Instead, once part of the wait has elapsed, the
governor allows a single probe request per record: success clears the
record; a 420 rewrites it from the server's own fresh `retry_after`. Both
outcomes convert a guess into ground truth for the price of one request,
automatically, with no button. A failed probe re-arms the record and
grants a fresh probe allowance, so storm-freedom is structural — every
probe requires a new server-confirmed deadline first, not a guard that has
to be maintained. See Assumption 1 below for what this rests on.

### 2. The governor wraps Telethon's private `_call`, not the public `__call__` (#134)

Installed once at client construction (`session._make_client`). Every
request — high-level helpers, `iter_messages` internals, raw
`client(request)` from `tg api`, and media downloads/CDN redirects — passes
through `_call` before it reaches the network and returns to it (result or
exception) afterward, which is exactly what the type-keyed gate needs to
see. Telethon's own `flood_sleep_threshold` is set to `0` for **every**
client, not only the three `mutation_safe=True` clone paths as today;
`request_retries` is left at Telethon's default, since it governs
transient server errors, not flood. The private-API dependency is held by
a boundary test asserting `_call`'s existence and signature, plus fail-fast
client construction if it is absent — a Telethon upgrade that moves it
breaks CI, not a live run.

*Why `_call` and not the public `__call__`.* `__call__` delegates to
`_call`, but not everything goes through `__call__`:
`telethon/client/downloads.py:89/96/97` and
`telethon/client/messages.py:1243` invoke `_call` directly. Wrapping the
public entry point would miss media downloads and CDN redirects entirely —
media transfer is its own paced class under decision 3.

*Why not per-call-site adoption*, i.e. leaving each command to opt in as
`with_cooldown` does today. It is what exists today, and #122 already
measured the result: two command families protected, roughly twenty not,
plus five sites that swallow `FloodWaitError` in a bare `except Exception`.
Under a `_call` wrapper a newly added command is governed whether or not
its author knew the governor exists — the same "nothing to forget" shape
as decision 1.

*Why switch off Telethon's own flood sleep everywhere, not just for
mutations.* Non-mutation clients run Telethon's default
`flood_sleep_threshold=60`, pinned today by
`tests/test_session.py:290`. That threshold makes Telethon silently
absorb any wait up to 60 seconds inside the RPC — invisible to the wait
budget, the checkpoint, and the journal. With the governor now owning
waiting deliberately (decision 3), two independent sleepers in one funnel
guarantee double-sleeping and unaccountable time; the governor must now
itself absorb the short floods reads previously got for free from the
library.

*Why a boundary test and fail-fast construction, not a hard version pin.*
A pin would freeze security fixes and new API layers to hold a risk a test
holds just as well. Failing to start when the seam is missing is the
correct failure mode here — an ungoverned run silently proceeding is
exactly what produced the 21.5-hour penalty.

### 3. Pacing is a persisted minimum interval per request type, generalizing `resolve_phone`, plus a rolling breadth budget across runs (#135)

The pace is a **persisted minimum interval per Telegram request type**,
the same key the cooldown uses. It generalizes
`src/tgcli/resolve_phone.py`'s already-proven pattern — a persisted
timestamp under an `flock`, a backward-clock clamp, a fixed interval
enforced *before* any flood occurs — from one method and one constant to a
per-request-type table. Defaults: history reads 3 s; `get_messages` by id
10 s per 300 ids; media transfer 3 s per file; dialog enumeration 3 s;
mutations and metadata lookups none (except `resolve`'s existing 3 s).
Peer breadth becomes a **windowed budget across runs** — 100 distinct
peers touched by history reads per rolling 24 hours — rather than a
per-invocation cap; exhausting it is a normal stop (exit 0, checkpoints
intact, resume time reported), not an error. The probe from decision 1
fires at 50% of a recorded wait having elapsed.

*The interval is measured start-to-start, and that binds the `_call`
wrapper.* The per-type timestamp is written **when the request is about to
be dispatched, not when it returns** — a reservation taken under the lock
before the RPC leaves, exactly as
`enforce_resolve_phone_cooldown()` already does
(`src/tgcli/resolve_phone.py`, which writes the timestamp inside the
pre-flight check and whose own comment calls it a reservation). Stated
explicitly because decision 2's seam pulls the other way: a `_call` wrapper
must handle flood exceptions *after* the wrapped call anyway, so stamping
on return is the locally natural thing to write, and an implementer
following decision 2 faithfully could land end-to-start pacing without
noticing it contradicts the precedent this decision cites. The difference
is not cosmetic — with a median request latency near 1.8 s, an end-to-start
3 s interval yields ~4.8 s between request starts, roughly 60% slower than
intended. Where a request's latency itself exceeds the interval, no
additional sleep is owed; the interval is a floor on start-to-start
spacing, not an added delay.

*The reservation is claimed atomically against a fresher competitor.*
Because tgcli runs are short-lived one-shots, two processes may race the
same slot; the persisted row is claimed with a conditional upsert that
refuses a stamp at or before the one on record (strictly *before* — an
identical instant is refused, not overwritten), and the loser waits to the
winner's slot plus the interval and retries until its claim lands. Known
limit of the protocol: two processes that both read "nothing reserved"
before either claims can each dispatch with sub-millisecond spacing (both
stamps are fresh, so neither loses). It is bounded by the scheduler, below
the interval, and self-healing — the next claim is ≥ one interval away —
and fixing it would require row locks, which this ledger deliberately
avoids.

*Why not rely on Telethon's own `wait_time`.* It covers only
`RequestIter`-based calls, not one-shot RPCs, and its state lives in the
process and dies with it. tgcli is short-lived one-shots by design (no
daemons); library-only pacing is structurally blind to a schedule — an
hourly launchd refresh would start every hour with a clean conscience,
regardless of what the account did an hour ago.

*Why the sustained norm (3 s / ≤20 RPC/min), not the widely-quoted 1 s
burst rate.* #123 documents two different numbers: Telethon's 1 req/s is
the pace within one bounded iterator ("read me this chat"), while the
empirical "~30 s flood wait per 10 requests" gives a sustained safe norm of
≤20 RPC/min over account-lifetime use. The governor drives a multi-day
campaign, not a single bounded read, so the sustained figure governs. A
two-rate scheme (burst rate for short reads, sustained rate for long
campaigns) was rejected because it would force the gate to infer the
caller's intent instead of just consulting the request type.

*Why a windowed budget instead of the existing per-run cap
(`--max-dialogs`).* A per-run cap already existed and the incident ran at
its ceiling (100), then simply ran again — the journal shows dozens of
consecutive `archive` invocations that afternoon. This is the third time
the map hit the same structural flaw (after the interval and the
cooldown): anything measured "per run" measures nothing in a tool made of
cheap, repeatable one-shots. `--max-dialogs` remains as the per-invocation
ergonomic knob; the windowed budget is what actually binds.

*Consequence stated plainly.* A 791-dialog backfill is now an inherently
multi-day job; the tool stops pretending it is one operation that can be
retried harder. The hourly `archive refresh` cadence (ADR-0068/ADR-0070)
becomes the budget's natural driver — wake, take what the budget allows,
stop. See Assumption 2 below: the 100-peer/24h number is a hedge, not a
measured limit.

### 4. Account state moves to SQLite; the journal gains fields; `doctor` gains a check; a cooldown wake is a normal exit 0 (#136)

The cooldown-per-type-and-probe-state, the per-type last-request timestamp,
and the windowed peer-breadth set move to **SQLite** — a row per request
type, a table of peers with timestamps — replacing the single
`clones/account-<id>.json` file. `invocations.jsonl` gains: `retry_after`,
the request type involved, the refusal's **provenance**, the **stop
reason**, total governed sleep, and the request count — still one line per
invocation, not per RPC. `doctor` gains a cooldown check (today's eight
checks, `commands/doctor.py:136-145`, contain nothing about flood state).
A scheduled run that wakes into a cooldown does what free request types
allow, records the rest as deferred, and **exits 0** with a deferred
report; the alert fires once, at arming, not on every wake.

*Why SQLite, not a richer JSON record.* The rolling breadth window is an
**accumulating** value, and `atomic.replace_text`'s read-modify-write with
no lock is simply wrong for one — #137's inventory found two concurrent
writers can race on the existing file (last `os.replace` wins, no merge),
and the incident's own role-session overlap on 2026-07-27 proves this is
not theoretical. SQLite's WAL locking removes the race; a rolling window
becomes an ordinary query instead of hand-rolled JSON arithmetic. The
precedent is already in the tree (ADR-0017's clone/archive stores). This
also ends a standing asymmetry: `resolve_phone`'s 3-second cooldown is
`flock`-protected; the 21.5-hour account cooldown was not.

*Why a per-invocation summary, not a per-request event log.* The governor
now touches every RPC; one backfill would otherwise produce thousands of
journal lines and a second journal with its own rotation concerns.
Detailed pacing behavior is what #140's canary instrumentation is for,
scoped to that one run, not a standing cost on every invocation.

*Why exit 0 on a cooldown wake, not exit 5.* Two earlier decisions already
established the shape: an exhausted breadth budget is a normal stop
(decision 3), a reached wall-clock cap is a normal stop (decision 6). A
cooldown is the same kind of state — "not now, come back later" — not a
failure. Today an hourly refresh under an 18-hour cooldown exits 5
eighteen times, which under ADR-0070's failure-streak notification means
eighteen identical alerts for one event, and alerts that repeat like that
stop being read. Partial progress is possible precisely because decision 1
made cooldowns independent per type: a refresh can find history reads
penalized while dialog enumeration is free, and do the free part.

### 5. The budget is account-scoped and shared across every session role; the probe is marked spent write-ahead (#137)

One governor state per account, shared by the primary session and every
named role (ADR-0062). The session lock itself is untouched — a primary
and a role session still each get their own `flock` and genuinely run
concurrently; the governor changes nothing about locking. When a probe
(decision 1) is issued, the record is marked **spent before the attempt**,
not after.

*Why account-scoped, accepting that two concurrent lanes share one pace.*
A per-role budget would be defeated by adding one more session, and #123
is explicit that a second session buys nothing at the server — 420 is
keyed to the account, and a second session "solves lock contention, not
flood." A per-role budget would let an operator accelerate past a limit
Telegram still enforces, making tgcli lie about safety while changing
nothing about the actual risk. The accepted cost is starvation bounded by
the shared interval (3 s at these defaults) — not enough to justify a
priority mechanism.

*Why not quietly route long jobs onto a role session to keep interactive
work free.* ADR-0062 forbids exactly this: "No implicit fallback in either
direction... otherwise 'which device did this' is unanswerable in the
audit log." That reasoning is not revisited here.

*Why write-ahead, not mark-after-success.* Decision 1 made storm-freedom
structural rather than guarded; marking the probe spent after the outcome
would reopen the hole it closed — a process dying between the request and
the write leaves the probe unspent, so every retry probes again, and a
crash loop becomes a slow retry storm against an account already under
penalty. Crashes are not hypothetical: the incident alone contains two
deadline kills. The accepted cost is that a crash mid-probe forfeits that
probe and the run waits out the full recorded deadline — bounded and
recoverable, since a later 420 re-arms the record with a fresh probe.

*Why the rest of the crash contract needed no separate design.* A
last-request timestamp simply ages under a crash; breadth-window rows and
the cooldown itself survive a crash and remain correct (those peers really
were read, whether or not the process lived to say so); the session flock
releases on any process death, including `SIGKILL`, as a kernel guarantee.
This falls out of decision 4's storage choice: state expressed as
timestamps and rows, not counters, has no partially-spent condition to
repair.

### 6. The invocation deadline becomes a hang detector; both exemption mechanisms are deleted; long commands get an explicit wall-clock cap (#138)

Time the governor spends sleeping **by its own decision** — a pacing
interval, absorbing a flood — no longer counts against the `--timeout`
deadline. The deadline exists to catch a wedged network or a stuck call,
not to punish deliberate waiting. **Both** exemption mechanisms disappear
entirely: `_default_timeout()` (`cli.py:91-110`) and its independent
sibling `_long_running()`/`_deadline()` (`cli.py:128-139`, which this map
found only after the ticket was filed narrowly against the first one).
Long-running commands instead carry an **explicit wall-clock cap**,
independent of the breadth budget; exhausting it is a normal stop (exit 0,
checkpoints intact, resume pointer reported) exactly like exhausting the
breadth budget. `SHORT_WAIT` (60) and `WAIT_BUDGET` (180) are retired: a
recorded wait is slept out if it fits in what remains of the run's
wall-clock cap; otherwise the run exits 5 with `retry_after` and the
schedule resumes it later.

*Why an exemption list cannot survive, once the governor sleeps
deliberately and constantly.* At decision 3's defaults, an ordinary `read
--limit 1000` is ten pacing windows — about 30 seconds of pure sleeping
before any flood even occurs — and a hundred-dialog backfill exceeds a
60-second deadline by orders of magnitude on pacing alone. An exemption
list would have to cover nearly everything, and there were already two
independently drifting lists (one missed when this map was first charted).
Turning the deadline into a hang detector removes the exemption question
entirely rather than answering it — the same shape as decision 1 (nothing
to forget to classify) and decision 2 (nothing to forget to wire up).

*Why an explicit wall-clock cap, independent of the breadth budget, rather
than deriving one duration from the other.* With the deadline no longer
bounding wall time, run duration would otherwise be an accident of two
unrelated numbers: at decision 3's defaults, 100 dialogs × ~10 windows ×
3 s lands near 50 minutes, which fits the hourly launchd cadence
(ADR-0068) only by coincidence. Decision 3 states plainly that the
breadth budget may be *raised* once #140's canary produces evidence.
Raise it, and runs would silently outgrow the schedule, with a scheduled
run arriving on top of one still going — an invariant resting on the
coincidence of two unrelated numbers, which fails eventually and fails
quietly.

*Why retire `SHORT_WAIT`/`WAIT_BUDGET` instead of keeping them alongside
the new cap.* `WAIT_BUDGET` bounded how long one invocation could sit
sleeping; the wall-clock cap now does that job, and keeping both would
duplicate the mechanism with a second number. `SHORT_WAIT` was the
boundary between waiting a flood out and exiting 5, inherited from
Telethon's own `flood_sleep_threshold` — which decision 2 just switched
off, orphaning the constant from its justification. The new rule needs no
constant of its own: sleep if the wait fits what remains of the cap,
otherwise exit 5.

*Consequence for the incident itself.* The two `TIMEOUT` kills at the
default 60-second deadline were a run being punished for waiting
correctly. Under this decision the clock would not have been running
during that wait; had the wait genuinely not fit the cap, the run would
have stopped cleanly with its cursor advanced, rather than being killed by
a `BaseException` that bypasses `backfill.py`'s own checkpoint handler.

## Assumptions nobody could verify

Two assumptions carry real design weight and neither could be confirmed
against any primary source, the four MTProto client implementations
checked (Telethon, Pyrogram, TDLib, MadelineProto), or the incident's own
data. Both are stated here deliberately rather than left implicit.

1. **Whether a request issued during an active penalty extends that
   penalty.** The self-verifying probe (decision 1) rests on this being
   false, or at least bounded: it issues exactly one request into a
   record that might still be live. #132's source search found nothing
   documenting the answer either way. **If this assumption is wrong**,
   the cost is bounded by construction, not unbounded: each probe requires
   a fresh server-confirmed deadline before it can fire again, so a wrong
   assumption costs one extra request per confirmed deadline — never a
   loop, never a storm. **What would falsify it**: a probe's own outcome
   directly answers the question for that record — if a probe sent late in
   a wait comes back with a *longer* `retry_after` than the time already
   elapsed would explain, the request extended the penalty. #140's canary
   was explicitly built to produce zero penalties (its stop rule aborts on
   any flood-family error before a follow-up request could be sent), so it
   could not observe this by design; **it ran on 2026-08-02, drew no
   penalty, and duly produced no evidence here.** Settling it needs a
   separately-approved provocation experiment, not folded into this ADR or
   into #140. This assumption is as open now as when it was written.

2. **Whether the multi-hour penalty keys on request rate or on
   distinct-peer breadth.** #132 found the two hypotheses perfectly
   confounded in the incident's own data — Telethon's throttle never
   engaged at `--limit 1000`, and tgcli added none, so the run was
   maximally unpaced across all 791 peers at once. No primary source or
   checked client implements or documents a breadth-keyed limiter distinct
   from the standard per-(method, params, account) bucket. **The 100
   peers/24h breadth budget in decision 3 is a hedge against this
   hypothesis, not a measured limit.** **What was tried, and what came
   back**: #140's canary held rate fixed and varied breadth (3 peers in
   Phase A, 12 in Phase B, identical pacing). A `FLOOD_WAIT` after Phase B
   but not Phase A would have been the cheap positive signal for
   breadth-sensitivity; one during Phase A would have meant the sustained
   default is already marginal independent of breadth, the more serious
   finding. **Neither happened: the two phases were indistinguishable on
   every metric and no flood occurred at all.** As pre-registered, absence
   of a `FLOOD_WAIT` at that scale is a negative result, not proof breadth
   is irrelevant — it fails to falsify the hypothesis rather than
   confirming its absence, and 15 peers is 15% of the budget against an
   incident that spanned 791. The cheapest available signal has now been
   spent and returned null; anything further costs a deliberate
   provocation. **If this assumption is
   wrong** (breadth genuinely does not matter, or matters at a much higher
   threshold than 100/24h), the fix is a one-line config change: raise the
   budget. If it is wrong in the other direction (breadth matters at a
   *lower* threshold than 100/24h), the incident's own severity is the
   lower bound on how wrong that could be, and the budget would need to
   come down, not up.

## Consequences

- **`docs/CONTRACT.md`** changes in the same commit as the implementation
  (not in this ADR): the `--timeout` row loses its hand-listed exemptions
  in favor of "governed sleep does not count against the deadline, plus an
  explicit wall-clock cap for long-running commands"; exit code 5's row
  gains a note that a scheduled run under a cooldown may instead exit 0
  with a deferred report; §9's invocation-journal field list gains
  `retry_after`, request type, provenance, stop reason, governed sleep,
  and request count; §5.1's `doctor` check list gains a cooldown entry.
  The exact before/after text is proposed in this ticket's tracking
  comment on #141, not in this file, per that ticket's scope.
- **A public-contract break, not merely an addition.** A scheduled
  `archive refresh` that previously exited 5 under a cooldown now exits 0
  with a deferred report for the identical trigger condition. Any consumer
  polling exit code 5 to detect "the account needs to wait" will observe
  different behavior after this ships. Per ADR-0038 rule 2 ("a contract
  break is major and needs its own ADR"), this ADR is that ADR; the
  release bookkeeping in #141's tracking comment flags the version
  question for the integrator rather than deciding it here.
- **`SHORT_WAIT` and `WAIT_BUDGET` are retired**, and with them the whole
  `with_cooldown`/`FloodGate`/`WaitBudget` mechanism in `clone/flood.py`
  and `clone/cooldown.py` — superseded by the `_call` wrapper (decision 2)
  and the deadline-as-hang-detector plus explicit wall-clock cap
  (decision 6). `tests/test_clone_flood.py` and
  `tests/test_clone_cooldown.py` are rewritten wholesale against the new
  seam, per #139's test matrix.
- **Both deadline exemption mechanisms are deleted**: `_default_timeout()`
  and `_long_running()`/`_deadline()` (`cli.py:91-110`, `cli.py:128-139`).
  No replacement list is introduced — the deadline's new definition (hang
  detector, paused during governed sleep) applies uniformly.
- **ADR-0045 decision 1** (the single-file, clone-only account cooldown
  and its `cooled_account`/`enforce_account` enforcement) is superseded by
  decisions 1 and 4 above: the mechanism generalizes from "clone mutations
  only" to "every gated request type," and the storage generalizes from
  one JSON file to the SQLite ledger. ADR-0045 decisions 2
  (`clone init --no-comments`) and 3 (preview flood-risk hints) are
  unaffected in behavior; decision 3's `account_flood` preview field is
  narrowed in source — it now reflects the specific request type(s) a
  clone commit is about to issue against the new ledger, rather than the
  single account-wide value the old file held. Migrating the existing
  `clones/account-<id>.json` record (and its odd location under
  `clones/` for a value that governs the whole account) is carried on
  map #131's "Not yet specified" list and belongs to the implementation
  slice, not this ADR.
- **ADR-0052 decisions 1–5** (the foreground short-wait-and-retry
  mechanism, its constants, arm-before-sleep ordering, and visible
  progress line) are superseded wholesale by decision 6 above. ADR-0052
  decisions 6–7 (the reupload media cache under
  `~/.local/state/tgcli/clones/<clone_id>-media/`) are untouched — media
  file reuse across a failed batch is orthogonal to request pacing.
- **The recurring shape was deliberate.** Every decision above removes a
  condition under which a mechanism could silently stop applying —
  per-type keying instead of a class list to maintain (decision 1), a
  universal seam instead of per-call-site adoption (decision 2), a
  persisted interval instead of process-local state (decision 3), state
  that cannot silently lose an update (decision 4), a write-ahead mark
  instead of a race window (decision 5), no exemption list at all instead
  of one more line to remember (decision 6). This is not incidental
  consistency: #122 found that today's roughly twenty ungoverned request
  sites, and the five that silently swallow `FloodWaitError`, arrived
  through ordinary forgetting — nobody classified a new command when it
  was added, not through any decision to leave it exposed. A design that
  keeps giving a mechanism one more list to stay in sync with just
  reproduces that failure under a new name. Removing the condition instead
  of adding a mechanism to remember is why request type (not a maintained
  class list) is the gate key, why the seam is one wrapper (not per-call
  adoption), and why the deadline has no exemptions left to drift.
- **Follow-up scope, explicitly deferred, not decided here**: per-method
  request weight/cost; explicit per-run RPC budgets for the unbounded
  pagination sites (`export subscribers`, `export messages` without
  `--limit`, `media download --bulk`, `clone sync` full exhaust,
  `changes once` drain loop); a budget for `changes --wait`'s long-poll
  loop; whether the hourly `archive refresh` cadence itself needs to
  change under the new pacing; whether `dialogs`-family calls (where flood
  is Telegram's documented throttling mechanism, not a penalty) need a
  policy distinct from history reads. All carried on map #131's "Not yet
  specified" list.

## Evidence from #140

Decisions 1, 2, 4, 5 and 6 in full, and decision 3's mechanism, were never
contingent on the canary; #140 tested execution, not the shape of these
choices. What follows is what the canary did and did not establish about
the parts that were.

The canary ran on 2026-08-02 against the account that took the incident,
17 h after its 21.5 h penalty expired and after the account had been used
normally in between. 27 requests (24 history reads, 2 dialog enumerations,
1 get-messages-by-id) across 15 distinct peers, 2130 messages, zero
mutations, zero media transfers, 138 s wall clock. **All 27 exited 0. No
`FLOOD_WAIT`, no flood-family error, no `retry_after`.** Full protocol,
approval and evidence: issue #140.

**What it supports.** A bounded, paced bulk read of this shape draws no
penalty on a recently-penalized account. Nothing observed contradicts any
decision here.

**What it does not support, and what therefore stays provisional even at
`accepted`:**

- **The 3 s history-read interval, as this ADR defines it.** The canary
  paced by sleeping *after* each request returned, so its realized
  start-to-start interval was a median 4.84 s, not 3 s. Decision 3 mandates
  start-to-start; the canary therefore exercised the *looser* of the two
  readings. The 3 s default still has no live evidence behind it. This
  gap is the direct reason decision 3 now states its measurement basis
  explicitly.
- **The 100 peers/24h breadth budget.** The canary touched 15 peers — 15%
  of the budget. It shows the budget was not approached, not that it is
  correctly placed.
- **The 10 s per 300 ids, 3 s media, and 50% probe-fire numbers.** One
  get-messages-by-id sample; no media transfers; no penalty and therefore
  no probe. Untested.
- **Assumption 2 (rate vs. breadth): null result.** Phase A (3 peers) and
  Phase B (12 peers) at identical pacing were indistinguishable — median
  gap 4.84 s in both, median duration 1822 vs 1824 ms, zero errors in
  both. As pre-registered, this fails to falsify the hypothesis rather
  than settling it; the incident spanned 791 peers, two orders of
  magnitude beyond this test. The budget remains a hedge.
- **Assumption 1 (does a request during a penalty extend it): unanswered,
  as designed.** No penalty occurred, and #140's stop rule forbade the
  follow-up request that would have observed one. Settling it still needs
  a separately-approved provocation experiment. This does not block
  acceptance — decision 1's probe is bounded either way — but this ADR
  claims no confidence in it.

Accepting this ADR therefore accepts the *mechanism and its reasoning*.
The specific numbers in decision 3 remain the sustained norm computed from
#123's research, carrying one live demonstration that they are not
catastrophically wrong.

## Test coverage

The seam, gate-membership, pacing, deadline, ledger, and concurrency test
rows specified in #139's accepted matrix constitute the acceptance test
suite for the implementation slice this ADR authorizes. This ADR does not
add test code; it authorizes the matrix as the specification.
