# Schedule an archive refresh

`tg archive refresh` is a bounded foreground job that composes the archive
delta sync, media acquisition, and local Parakeet queue. It exits after one
pass, so it is safe to call from launchd or another scheduler.

```bash
tg --json archive refresh
tg --json archive refresh \
  --max-events 500 --max-dialogs 20 --max-media 50 \
  --transcribe-limit 20 --max-attempts 3
```

The caps are per run: sync accepts at most 5000 catch-up messages, 50
dialogs, and 500 media items; transcription accepts at most 100 items and 5
attempts. All values must be positive. There is no unlimited value. The
job's requests are paced by the request governor (ADR-0072): history reads
and dialog enumeration wait 3 s between requests, and `archive refresh`
keeps no implicit 60-second deadline — an explicit `--timeout` acts as a
hang detector whose governed sleep does not count against it, not a job
bound. Pass `--max-runtime` when the whole pass must fit a wall-clock
budget: a wake whose cap is already exhausted defers sync and exits 0 with
a `stop_reason: "wall_clock_cap"`, and the schedule resumes the next pass.

A network failure or unavailable local transcription engine increments the
account's refresh failure streak. Item-level media/transcription failures are
returned in the completed stage data and exit nonzero, but do not increment the
account-level streak. A `FLOOD_WAIT` arms a per-request-type cooldown in the
governor's ledger and exits with its normal rate-limit result without
incrementing the streak. Any completed pipeline resets the streak. After
three consecutive run-level failures, macOS receives one generic notification;
it is not repeated until a completed run starts a new failure episode. Inspect
the state with:

```bash
tg --json archive status
```

## When the account is cooling

A `FLOOD_WAIT` is recorded **per request type**, so "the account is cooling"
is no longer a single state. `tg doctor` reports every active cooldown with
its deadline; a cooling type refuses locally with exit 5 and `retry_after`,
without any network call, until its deadline passes (the governor probes once
at half the wait, so an early-lifted limit is noticed automatically).

A scheduled refresh that wakes into a partial cooldown does what the free
request types allow, reports the rest as deferred, and **exits 0** with
`stop_reason: "cooldown_deferred"` and `deferred: ["sync"]` — it is a
success that deferred work, not a failure to alert on. The alert (the
stderr line) fires once, when the flood arms; later wakes are silent.
A refresh that is fully blocked stops normally and the schedule resumes
it later.

The `status.refresh` object contains `failure_streak`, `last_error`, the
notification threshold, and `notification_sent`. Notification delivery is
best-effort and does not put exception details or message text in the desktop
message.

## Manual launchd setup

The repository ships [a plist template](../assets/tgcli-archive-refresh.plist).
It is intentionally not installed or loaded by `tgcli`. Copy it to the
per-user LaunchAgents directory, replace both absolute-path placeholders, and
then load it yourself:

```bash
cp docs/assets/tgcli-archive-refresh.plist \
  "$HOME/Library/LaunchAgents/com.tgcli.archive-refresh.plist"
# Edit the copied plist: set the executable path and two log paths.
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.tgcli.archive-refresh.plist"
launchctl kickstart "gui/$(id -u)/com.tgcli.archive-refresh"
```

`StartInterval` is 3600 seconds, and the template's pass also carries
`--max-runtime 3000` — below the interval, so a wake that finds the cap
already exhausted defers sync and stops (exit 0, `stop_reason:
"wall_clock_cap"`) instead of starting a pass that cannot finish.

The cap is checked **once, before dispatch** (CONTRACT §13): a running sync
or transcription is never interrupted, and every launchd fire is a fresh
process with a fresh budget — so a pass that outruns the interval still
holds the session lock when the next run fires. The hard bounds on a
scheduled pass are the per-command caps (`--max-events`, `--max-dialogs`,
`--max-media`, `--transcribe-limit`); a true mid-run wall-clock bound is
tracked as a proposal in PROPOSALS.md. The one mid-run effect of
`--max-runtime` is flood-wait gating: a `FLOOD_WAIT` longer than the
remaining budget exits 5 immediately (a normal rate-limit result, no failure
streak) instead of sleeping it out. To stop the job without deleting the
template, unload the copied file:

```bash
launchctl bootout "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.tgcli.archive-refresh.plist"
```

The launchd job needs the same account configuration and authorized session
as a foreground invocation. Run one manual `tg --json archive refresh` first
and verify `tg --json archive status` before scheduling it.

See [the archive guide](archive.md) for store scope, backfill, search, and
transcription details, and [CONTRACT.md §13](../CONTRACT.md) for the stable
JSON and exit-code contract.
