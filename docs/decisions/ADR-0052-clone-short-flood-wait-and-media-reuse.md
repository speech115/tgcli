# ADR-0052: Surviving a long clone run — short flood waits and media reuse

Date: 2026-07-25
Status: accepted; decisions 1–5 (the `SHORT_WAIT`/`WAIT_BUDGET`
foreground-retry mechanism) superseded by
[ADR-0072](ADR-0072-account-request-governor.md). That supersession is
declared but **not yet effective**: ADR-0072 is accepted as a design and
its governor is unimplemented, so this mechanism is still what actually
runs and stays authoritative for current behaviour until the
implementation slice lands. Decisions 6–7 (the reupload media cache) are
untouched and remain in force.

Amends [ADR-0045](ADR-0045-clone-flood-containment.md) in one clause: what a
`clone sync` process does with a *short* `FloodWaitError`. The account-scoped
cooldown, its lazy enforcement at the next invocation, and the ban on retry
loops and background timers are unchanged.

## Context

Catching up the `[икона]` clone on 2026-07-25 took six `clone sync`
invocations. Five of them ended on a `FloodWaitError`, and the waits were
**3, 8, 3, 3 and 94 seconds** — four of the five under ten seconds. Each of
those exits threw away a warm process: a fresh connect, peer resolution, the
progress total RPC, and a fresh walk of the discussion group from the stored
cursor, all to honour a three-second pause. The run only completed because a
scratch shell loop restarted the command after each exit — the tool made the
human (or the agent) supply the one thing it refused to do itself.

`_with_cooldown` (`commands/clone.py:269-277`) catches `FloodWaitError`, arms
both cooldowns, and re-raises unconditionally; `grep sleep src/` returns
nothing. ADR-0045's prohibition is specific and worth quoting: "No daemons,
no background timers — the cooldown is enforced lazily at the next
invocation", and operationally, "exit 5 means wait it out, never retry in a
loop". Both aim at the same failure: a process that hammers Telegram, or
lingers, unattended. Neither addresses a foreground command pausing three
seconds and continuing the work it is already doing, in the invocation the
user is watching.

The second cost compounds the first. `_reupload_batch` downloads into a
`tempfile.TemporaryDirectory` (`commands/clone.py:677`) and the state cursor
only advances after the whole batch sends (`commands/clone.py:895-896`). A
`FloodWaitError` while uploading therefore discards every byte already
downloaded for that batch. On this source that is not a rounding error:
posts 89–94 carried videos of 526 MB and 499 MB, each on a `noforwards=true`
source where download+reupload is the only available path. One badly timed
rate limit costs a full re-download of half a gigabyte to re-send a file the
tool had already fetched. This run was lucky — the limits landed in the
comment phase, where messages are small.

## Decision

1. **A short flood wait is waited out in the foreground, once per call.**
   When `_with_cooldown` catches a `FloodWaitError` whose `retry_after` is at
   most `SHORT_WAIT = 60` seconds, it sleeps for that long plus a one-second
   margin and retries the *same* awaitable exactly once. A second
   `FloodWaitError` on the retry is raised as it is today.

2. **A per-run wait budget bounds the total.** `WAIT_BUDGET = 180` seconds
   per process. Once spent, every subsequent flood wait raises immediately
   regardless of length. Together with the single retry this makes the
   worst case explicit: a run can pause, but it cannot become a loop, and it
   cannot idle for an unbounded time.

3. **Both constants are constants.** No flag, no config key — ADR-0045's
   economy argument applies unchanged: a knob invites tuning loops in exactly
   the situation where the correct action is to stop.

4. **The cooldown is armed before the sleep, not after.** State is written
   first, so a process killed mid-sleep leaves the same durable cooldown it
   would have left by exiting. Nothing about the lazy next-invocation gate
   changes.

5. **The wait is visible.** The pause prints a progress line to stderr
   (ADR-0049, non-contractual) naming the seconds remaining, so a watching
   operator sees a waiting process rather than a hung one. `--json` stdout is
   untouched.

6. **Downloaded media survives a failed batch.** Reupload downloads go to
   `~/.local/state/tgcli/clones/<clone_id>-media/` instead of a temporary
   directory. A file is reused on a later run when its name and byte size
   match what the source reports; anything else is re-downloaded. The
   directory is deleted when the batch sends successfully, so the cache holds
   at most one in-flight batch.

7. **The cache is owned state, not litter.** It lives under the state dir
   that AGENTS.md already designates for cache, and `store cleanup` learns to
   report and remove it, so a run abandoned forever cannot silently keep half
   a gigabyte.

## Consequences

- The common case of this project — a protected source, a long catch-up,
  frequent three-second limits — completes in one invocation instead of six.
  The external retry loop that drove the `[икона]` run becomes unnecessary.
- The worst case is bounded and stated: at most 180 seconds of pausing per
  run, at most one retry per call, then exit 5 exactly as today. A run that
  hits a 94-second wait — as this one did — still exits, because 94 exceeds
  `SHORT_WAIT`.
- `clone sync` can now occupy a terminal for minutes without visible network
  activity. Decision 5 exists so that is legible; without it the change would
  trade a confusing exit for a confusing hang.
- Media reuse turns the batch from "atomic and expensive to retry" into
  "atomic and cheap to retry". It does not change what is sent: the send path
  and the cursor semantics are untouched.
- The cache can hold a half-gigabyte file between runs. That is the point,
  and decision 7 is what keeps it from being a leak.
- Tests must not sleep. The sleep goes behind an injectable seam so the
  suite asserts *that* the wait was requested and for how long, never by
  waiting.
