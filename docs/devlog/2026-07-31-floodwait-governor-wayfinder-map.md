## 2026-07-31 — Wayfinder map re-charted: account-wide request governor (Claude Opus 5)

**Did:** owner hit a 77,586 s (21.5 h) `FLOOD_WAIT` and asked to focus on the
FloodWait item in #120 via `/wayfinder`. Found map #121 already charted earlier
the same day (16:44) and bulk-closed `NOT_PLANNED` at 16:55 — fifteen seconds
after its two research children completed, with no comment and no devlog entry.
Owner ruled it an abandoned attempt and chose to re-chart rather than reopen.
Charted map #131 "Wayfinder map: account-wide Telegram request governor" with
ten children #132–#141, wired as native sub-issues with `blocked_by` edges.
Frontier: #132, #133, #136, #138. Fired a `/research` subagent on #132.
Cross-linked #120 and marked #121 superseded. Research findings on #122/#123
are reused as facts from the map's Notes rather than re-cut as tickets.

**Decided:** destination is an accepted ADR for an account-wide **request
governor** — gate membership and request classes, the code seam, pacing and
defaults, the flood ledger, cross-process coordination, deadline reconciliation,
and flood observability. Three owner decisions shaped it. (1) Observability is
*inside* the destination, not a follow-up: the incident could only be
reconstructed by arithmetic on a state file. (2) The `--timeout` interaction is
inside the map as its own ticket (#138) — not as "raise the timeout", which #120
names a non-goal, but as removing a contradiction between two subsystems. (3) The
job model / scheduler / priorities from #120's follow-up comment are explicitly
**out of scope**; they stand on the governor rather than contain it, and get
their own map once this ADR is accepted. Map #121's destination omitted them
too, so nothing is lost by the re-chart.

**Learned:** the forensics changed the problem statement, and `invocations.jsonl`
turned out to be the only witness. `cooldown_until` minus 77,586 s lands exactly
on the `archive` invocation at `14:54:41Z`, which pins the arming call. Three
findings fell out that neither #122 nor #123 had. First, the long wait was
preceded by two runs killed at the default 60 s deadline — and `src/tgcli/cli.py:98`
exempts `clone init/sync/refresh` from that deadline with the stated reason that
ADR-0052 lets them sleep up to 61 s, which "never fits inside a 60s default
deadline". `archive backfill` sleeps up to 61 s per dialog on its own
`WaitBudget` and was never exempted, so a backfill that waits a short flood out
is killed by its own default deadline, deterministically. Second, `retry_after`
is recorded nowhere. Third, a locally-gated exit 5 and a server-issued 420 are
indistinguishable in the logs — the `clone`/`send` rows at 17:38–17:40 cannot be
classified even in principle.

The owner's question "does this actually reduce risk globally?" exposed a real
hole in the first breakdown: it went straight to pacing defaults without
establishing *what earns a multi-hour wait*. #123 documents a typical
`getHistory` flood of ~30 s; 21.5 h is two orders of magnitude larger and
plausibly a different limiter — possibly keyed on distinct-peer breadth (791
private dialogs) rather than request rate. If so, rate-based defaults would not
prevent a recurrence. #132 was added ahead of the pacing ticket to settle it,
and #135 now blocks on it. Worth generalising: a decision ticket that assumes
the mechanism is a decision ticket resting on an unasked research question.

**Next:** work the frontier one ticket per session. #132 is claimed and running;
#133 (gate membership) is the root the rest of the map hangs on. The account
stays under server cooldown until 2026-08-01T12:27:47Z, so #140's live canary
cannot run before then.

**Addendum 2026-08-01 — subagent wave, and two corrections to the above.**
Ran #132 (research, resolved and closed) plus preparation briefs on #133, #134,
#136, #137, #138 as background subagents on cheaper models, verifying each
against the tree rather than relaying it. Two findings correct what this entry
first recorded.

First, the deadline contradiction is wider and its mechanism different.
`_make_client` (`session.py:128-150`) applies `flood_sleep_threshold=0` only for
`mutation_safe=True`; `archive backfill` therefore runs on a **default**
Telethon client that absorbs any wait <= 60 s inside the RPC and retries up to
`request_retries=5`. Because `flood.SHORT_WAIT` is also 60, backfill's own
`WaitBudget` branch is rarely reached — the sleeping is Telethon's, invisible to
the budget, the checkpoint and the log. A second exemption mechanism also
exists beyond `_default_timeout`: `_long_running`/`_deadline` (`cli.py:128-139`)
exempts `media` and `clone sync` when no explicit `--timeout` was given.

Second, and larger: **the incident run had no pacing at all.** Telethon's
`getHistory` throttle is armed by `self.wait_time = 1 if self.limit > 3000 else 0`
(`telethon/client/messages.py:165-166`). The run used `--limit 1000`, so
`wait_time` was 0 and the built-in 1 req/s pacing never engaged; `backfill` adds
none of its own across its 791 peers. This makes the breadth hypothesis
undeterminable — rate and breadth were perfectly confounded — and it means the
project was relying on a Telethon safeguard that its own default arguments
switch off. Also from #132: TDLib clamps flood waits to 14 days, so ADR-0052's
one-day ceiling (and `MAX_COOLDOWN_S`) rests on a weaker assumption than
believed.

A third correction landed on #136: `resolve_phone.py:50` is a **local** gate,
not a server raise, which means exit 5 with `retry_after` has three distinct
origins (server 420, account cooldown record, resolve-phone file) and the logs
distinguish none.

**Learned about the method:** every brief needed verification and two of four
carried a real error — one miscategorisation, one wrong mechanism. Delegation
moved the reading, not the checking.

**Addendum 2026-08-01 — map worked to the decision frontier.** #133, #134,
#135, #136, #137 and #138 all resolved in grilling sessions; #132 closed by
research. Only #139 (test matrix), #140 (canary) and #141 (ADR) remain, and
none of them requires another decision.

The governor as decided: cooldowns are independent **per Telegram request
type**, keyed exactly as Telethon's own `_flood_waited_requests` — persisting
across processes what the library already tracks within one — with the peer
deliberately excluded, because a breadth-earned penalty keyed per peer would be
recorded against whichever peer came last and let the next bulk sweep through.
`doctor` is the only exemption. The escape from a false record is a
self-verifying probe, one per record, marked spent write-ahead. Pace is a
persisted minimum interval per request type, generalizing `resolve_phone.py`'s
flock-and-timestamp pattern; peer breadth becomes a windowed budget across runs.
The governor wraps Telethon's `_call` (not `__call__`, which the download and
CDN paths bypass) and switches `flood_sleep_threshold` to 0 everywhere. The
deadline becomes a hang detector whose clock pauses during governed sleep, which
deletes both exemption tables; `SHORT_WAIT` and `WAIT_BUDGET` retire in favour
of "sleep it if it fits the run's remaining cap". State moves to SQLite, shared
account-wide across every role.

**Learned, and worth carrying:** six consecutive decisions came out as *remove
the condition under which the mechanism fails*, not *add a mechanism*. Nothing
to forget to classify; nothing to forget to wire up; a storm that is
arithmetically impossible rather than guarded against; no exemption table to
drift. That shape was chosen deliberately after #122 showed the twenty
unprotected commands arrived through ordinary forgetting, not neglect — so a
policy whose safety depends on future diligence would have reproduced the
failure it was written to prevent.

Two further consequences fell out rather than being designed. The crash contract
in #137 needed almost no work, because #136's decision to store timestamps and
rows instead of counters leaves no partially-spent state to repair. And a
791-dialog backfill is now explicitly a multi-day job driven by the existing
hourly launchd pass — a burst becomes a drip with no daemon, satisfying ADR-0002
without arguing with it.

**Still standing on an unverified assumption**, recorded in #133 and #135 and
required to appear in the ADR: nobody could establish whether a request issued
during a penalty extends it, nor whether the multi-hour penalty keys on rate or
on peer breadth. The breadth budget is a hedge, not a measured limit, and #140
is the only cheap evidence available.

**Addendum 2026-08-01, later — #139 accepted, ADR-0072 drafted, gate restored.**
The test matrix on #139 was verified (five cited test line references exact) and
accepted as the implementation slice's spec; its change-inventory counts were off
by one and two (13 and 15 tests, 28 collected, not 12 and 17), corrected in the
closing comment. #140's canary protocol landed with a genuinely controlled
design — phase A three dialogs x four windows, phase B twelve dialogs x one
window: identical request count and identical 3 s rate, breadth the only
variable, which is exactly the confound #132 could not separate. Its baseline
also confirms #132 from a second direction: of 132 `archive` invocations, all on
the incident day, 65 % of consecutive gaps are under 5 s — the log says
"unpaced" independently of what the code says.

`docs/decisions/ADR-0072-account-request-governor.md` is drafted at status
`proposed`, blocked on #140's evidence. Its supersession bookkeeping was checked
against the sources and is exact: ADR-0045 has three decisions and only the
first is superseded; ADR-0052 has seven and the cut falls cleanly between the
`SHORT_WAIT`/`WAIT_BUDGET` mechanism (1–5) and the reupload media cache (6–7).

Adding the ADR turned the docs gate red — four failures, which all proved to
trace to one cause after checking (removing the file returned 19/19 green): the
`docs/MAP.md` inventory row still read `ADR-0001…0071`. Fixed there, plus the
load-bearing fixture in `tests/test_check_docs.py:192/201` — the negative test
corrupts `MAP.md` on purpose, so leaving `0071` in its `replace` turns the test
into a no-op that fails for the wrong reason. This is the second time that
fixture has had to move with the index; it is doing its job. Gate: 1635 passed,
9 skipped.

#140 is approved with one amendment: the run waits a few hours past the
cooldown's expiry rather than starting at the boundary, because whether an
account stays sensitive immediately after a penalty lapses is another thing
nobody could establish. It remains blocked on the owner's `D1`…`D15` selection.

**Noticed, and worth recording:** ADR-0052 decision 4 already said "the cooldown
is armed before the sleep, not after" — the same write-ahead reasoning #137
arrived at independently for the probe. That is the third mechanism this map
found already present and merely under-applied, after `resolve_phone.py`'s
persisted interval and Telethon's own request-type keying. The pattern is
consistent enough to be worth checking first next time: before designing a
mechanism here, look for the place it already exists in one corner.
