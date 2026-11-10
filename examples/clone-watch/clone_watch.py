"""Keep clones current: run `clone sync` as soon as a source gets a message.

    tg --account ALIAS --session-role watch --timeout 5400 run clone_watch.py \
        SOURCE [SOURCE ...] [--sync-role job] [--run-for 3600]

Read-only. Telegram pushes new messages in each SOURCE to this session, and the
script asks once a minute for the newest message id of each SOURCE and its
discussion group, because a group the account has not joined pushes nothing;
each burst triggers `tg clone sync SOURCE` for the sources it
touched, one at a time on the --sync-role session, because the listener holds
its own session for the whole run. It syncs
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
parser.add_argument(
    "sources",
    nargs="+",
    metavar="SOURCE",
    help="clone source, as `tg clone sync` takes it",
)
parser.add_argument("--sync-role", default="job")
parser.add_argument("--run-for", type=float, default=3600)
args = parser.parse_args()

SYNC = [
    str(Path(sys.executable).with_name("tg")),
    "--account", account, "--session-role", args.sync_role,
    "--max-runtime", "240", "--json", "clone", "sync",
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


async def sync(source, reason):
    for attempt in itertools.count(1):
        proc = await asyncio.create_subprocess_exec(
            *SYNC,
            source,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        output = out.decode() + err.decode()
        if proc.returncode == 0:
            r = json.loads(out)["sync"]
            log(
                f"sync ({reason}): copied={r['copied']} cursor={r['cursor']} "
                f"discussion_cursor={r['discussion_cursor']} source={source}"
            )
            return
        last = (output.strip().splitlines() or [""])[-1]
        log(f"sync ({reason}): exit {proc.returncode} source={source}", last)
        if not retryable(proc.returncode, output):
            await notify(f"clone sync {source} exit {proc.returncode}")
            return
        if time.monotonic() + RETRY_DELAY > deadline:
            # the next start syncs again; one busy minute before it is no alarm
            if attempt >= ALERT_AFTER:
                await notify(
                    f"clone sync {source} kept failing, last exit {proc.returncode}"
                )
            return
        await asyncio.sleep(RETRY_DELAY)


async def sync_all(sources, reason):
    for source in sources:
        await sync(source, reason)


source_of = {}  # chat id -> the SOURCE argument whose clone it feeds
watched = []  # the same chats, as GetPeerDialogs takes them
for ref in args.sources:
    entity = await client.get_entity(int(ref) if ref.lstrip("-").isdigit() else ref)
    full = (await client(functions.channels.GetFullChannelRequest(entity))).full_chat
    chats = [entity]
    if full.linked_chat_id:  # the discussion group, where comments arrive
        chats.append(types.PeerChannel(full.linked_chat_id))
    for chat in chats:
        source_of[utils.get_peer_id(chat)] = ref
        watched.append(types.InputDialogPeer(await client.get_input_entity(chat)))
wake = asyncio.Event()
pending = set()
seen = {}  # chat id -> newest message id noticed there


@client.on(events.NewMessage(chats=list(source_of)))
async def on_new(event):
    log("new message", event.chat_id, event.id)
    seen[event.chat_id] = max(event.id, seen.get(event.chat_id, 0))
    pending.add(source_of[event.chat_id])
    wake.set()


async def poll():
    # Telegram pushes nothing for a discussion group the account has not
    # joined, so comments there are found by asking for the newest message id.
    try:
        request = functions.messages.GetPeerDialogsRequest(watched)
        dialogs = (await client(request)).dialogs
    except Exception as exc:
        log("poll failed:", type(exc).__name__, exc)  # the next tick asks again
        return
    for dialog in dialogs:
        chat_id = utils.get_peer_id(dialog.peer)
        if dialog.top_message > seen.setdefault(chat_id, dialog.top_message):
            log("new message", chat_id, dialog.top_message)
            seen[chat_id] = dialog.top_message
            pending.add(source_of[chat_id])
            wake.set()


deadline = time.monotonic() + args.run_for
log("listening", list(source_of))
await poll()  # the baseline: what arrives during the start sync counts as new
await sync_all(args.sources, "start")
while time.monotonic() < deadline:
    before = time.time()
    try:
        await asyncio.wait_for(wake.wait(), TICK)
    except TimeoutError:
        await poll()
        # a tick that took far longer on the wall clock means the Mac slept
        if time.time() - before > 2 * TICK:
            pending.clear()
            wake.clear()
            await sync_all(args.sources, "wake")
        continue
    await asyncio.sleep(DEBOUNCE)
    wake.clear()  # messages arriving during the syncs below set it again
    touched = [s for s in args.sources if s in pending]
    pending.clear()
    await sync_all(touched, "new message")
log("restart")
