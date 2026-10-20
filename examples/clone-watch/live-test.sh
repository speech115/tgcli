#!/bin/bash
# Live check of clone_watch.py against the real account: a busy sync session is
# retried, a simulated Mac sleep (SIGSTOP for longer than two ticks) triggers a
# wake sync, and the run ends cleanly at its deadline. Needs an initialized
# clone of SOURCE and the `watch` and `job` session roles; stop the launchd
# listener first, since it holds the watch session. Takes about 7 minutes.
#
#   examples/clone-watch/live-test.sh ALIAS SOURCE
set -u
ALIAS=$1 SOURCE=$2
TG=${TG:-tg}
HERE=$(cd "$(dirname "$0")" && pwd)
DIR=$(mktemp -d)
LOG=$DIR/watch.log
echo 'import asyncio; await asyncio.sleep(150)' > "$DIR/hold.py"

"$TG" --account "$ALIAS" --session-role job --timeout 200 run "$DIR/hold.py" &
sleep 3
( "$TG" --account "$ALIAS" --session-role watch --timeout 900 \
      run "$HERE/clone_watch.py" "$SOURCE" --run-for 330 >> "$LOG" 2>&1
  echo "rc=$?" >> "$LOG" ) &
until grep -q "sync (start): copied" "$LOG" 2>/dev/null; do sleep 2; done
sleep 10
PID=$(pgrep -f "clone_watch.py $SOURCE --run-for 330")
kill -STOP "$PID"; sleep 150; kill -CONT "$PID"
wait

cat "$LOG"
fail=0
for want in "sync (start): exit 3" "sync (start): copied" "sync (wake): copied" \
            "restart" "rc=0"; do
    grep -qF "$want" "$LOG" || { echo "MISSING: $want"; fail=1; }
done
[ $fail -eq 0 ] && echo "PASS"
exit $fail
