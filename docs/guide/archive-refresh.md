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
attempts. All values must be positive. There is no unlimited value. The job
uses one FloodWait budget for the network stage and has no implicit 60-second
deadline; pass an explicit `--timeout` when a shorter wall-clock limit is
needed.

A network failure, unavailable local transcription engine, or item-level
media/transcription failure increments the account's refresh failure streak.
The completed stage data is returned for item-level failures and the command
exits nonzero. A fully successful run resets the streak. After three
consecutive failed runs, macOS receives one generic notification; it is not
repeated until a successful run starts a new failure episode. Inspect the
state with:

```bash
tg --json archive status
```

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

`StartInterval` is 3600 seconds. To stop the job without deleting the
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
