# clone-watch

`tg clone sync` copies what is new and exits. This example keeps a clone
current within about half a minute of each new post or comment, without
polling: a read-only `tg run` script listens for new messages in the source
and its discussion group and runs `clone sync` after each burst.

tgcli itself stays daemonless. The listener is user-land: one `tg run`
script that launchd keeps alive.

## Setup

1. Initialize the clone (`tg clone init`) and run one `tg clone sync`.
2. Authorize two session roles beside the account's primary, so the listener
   never blocks your own commands:
   `tg accounts login ALIAS --role watch --phone PHONE` holds the listener's
   connection, and `--role job` runs the syncs it triggers.
3. Copy `com.tgcli.clone-watch.plist` to `~/Library/LaunchAgents/`, fill in
   `TG` (the absolute path of `tg`), `SCRIPT` (this directory's
   `clone_watch.py`), `ALIAS`, `SOURCE`, and `HOME`, then
   `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tgcli.clone-watch.plist`.

The log is `~/.local/state/tgcli/clone-watch.log`. A sync that fails for a
non-transient reason, or keeps failing for ten minutes, posts a macOS
notification.

## Behavior

- Each new message waits 25 s (an album or a quick edit lands whole), then
  `clone sync` runs on the `job` role. Messages that arrive during a sync
  trigger one more.
- A busy `job` session, a lost connection, or a flood cooldown is retried
  every minute.
- The script syncs on start and after the Mac wakes, and exits after an hour
  so launchd restarts it. Posts that appear while the Mac sleeps are copied
  on wake.
- Only new messages are copied, as with `clone sync`; edits and deletions in
  the source are not followed.

`live-test.sh ALIAS SOURCE` checks the retry, wake, and restart paths against
the real account in about seven minutes.
