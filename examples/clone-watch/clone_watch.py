"""Keep a clone current: run `clone sync` as soon as the source gets a message.

    tg --account ALIAS --session-role watch --timeout 5400 run clone_watch.py \
        SOURCE [--sync-role job] [--run-for 3600]

Read-only. Telegram pushes new messages in SOURCE and its discussion group to
this session; each burst triggers `tg clone sync SOURCE` on the --sync-role
session, because the listener holds its own session for the whole run. It syncs
on start and after the Mac wakes, which covers whatever arrived while it was
down, and exits cleanly after --run-for seconds so launchd restarts it fresh.
"""

import argparse
import asyncio
import itertools
import json
import sys
import time
from pathlib import Path

from telethon import events, utils

parser = argparse.ArgumentParser(prog="clone_watch.py")
parser.add_argument("source", help="clone source, as `tg clone sync` takes it")
parser.add_argument("--sync-role", default="job")
parser.add_argument("--run-for", type=float, default=3600)
args = parser.parse_args()

SYNC = [
    str(Path(sys.executable).with_name("tg")),
    "--account", account, "--session-role", args.sync_role,
    "--max-runtime", "240", "--json", "clone", "sync", args.source,
]  # fmt: skip
DEBOUNCE = 25  # let an album or a quick edit land before copying
TICK = 60
RETRY_DELAY = 60
ALERT_AFTER = 10  # failed attempts, about ten minutes


def log(*parts):
    print(time.strftime("%F %T"), *parts, flush=True)


def retryable(code, output):
    # 1 network, 2 clone lock held by another sync, 5 flood cooldown,
    # 3 only while another tg process holds the sync session.
    return code in (1, 2, 5) or (code == 3 and "is busy" in output)


async def notify(text):
    await asyncio.create_subprocess_exec(
        "/usr/bin/osascript",
        "-e",
        f'display notification "{text}" with title "tgcli clone watch"',
    )


async def sync(reason):
    for attempt in itertools.count(1):
        proc = await asyncio.create_subprocess_exec(
            *SYNC, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        output = out.decode() + err.decode()
        if proc.returncode == 0:
            r = json.loads(out)["sync"]
            log(
                f"sync ({reason}): copied={r['copied']} cursor={r['cursor']} "
                f"discussion_cursor={r['discussion_cursor']}"
            )
            return
        last = (output.strip().splitlines() or [""])[-1]
        log(f"sync ({reason}): exit {proc.returncode}", last)
        if not retryable(proc.returncode, output):
            await notify(f"clone sync exit {proc.returncode}")
            return
        if time.monotonic() + RETRY_DELAY > deadline:
            # the next start syncs again; one busy minute before it is no alarm
            if attempt >= ALERT_AFTER:
                await notify(f"clone sync kept failing, last exit {proc.returncode}")
            return
        await asyncio.sleep(RETRY_DELAY)


ref = args.source
source = await client.get_entity(int(ref) if ref.lstrip("-").isdigit() else ref)
full = (await client(functions.channels.GetFullChannelRequest(source))).full_chat
chats = [utils.get_peer_id(source)]
if full.linked_chat_id:  # the discussion group, where comments arrive
    chats.append(utils.get_peer_id(types.PeerChannel(full.linked_chat_id)))
wake = asyncio.Event()


@client.on(events.NewMessage(chats=chats))
async def on_new(event):
    log("new message", event.chat_id, event.id)
    wake.set()


deadline = time.monotonic() + args.run_for
log("listening", chats)
await sync("start")
while time.monotonic() < deadline:
    before = time.time()
    try:
        await asyncio.wait_for(wake.wait(), TICK)
    except TimeoutError:
        # a tick that took far longer on the wall clock means the Mac slept
        if time.time() - before > 2 * TICK:
            await sync("wake")
        continue
    await asyncio.sleep(DEBOUNCE)
    wake.clear()  # messages arriving during the sync below set it again
    await sync("new message")
log("restart")
