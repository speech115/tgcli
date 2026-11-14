"""examples/clone-watch: a new message found by polling triggers `clone sync`."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tgcli.cli import main

SCRIPT = str(Path(__file__).parents[1] / "examples/clone-watch/clone_watch.py")
SOURCE, DISCUSSION = 111, 222
CONFIG = 'default_account = "main"\n[accounts.main]\napi_id = 1\napi_hash = "h"\n'


class WatchClient:
    """A channel whose discussion group pushes nothing: only polls see it."""

    def __init__(self, discussion_tops):
        self.tops = list(discussion_tops)  # one per poll; the last one repeats
        self.sent = []
        self.handler = None

    async def get_entity(self, ref):
        return types.PeerChannel(SOURCE)

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(peer.channel_id, access_hash=5)

    def on(self, event):
        def register(handler):
            self.handler = handler
            return handler

        return register

    async def _call(self, sender, request, ordered=False):
        self.sent.append(request)
        if isinstance(request, functions.channels.GetFullChannelRequest):
            return SimpleNamespace(full_chat=SimpleNamespace(linked_chat_id=DISCUSSION))
        top = self.tops.pop(0) if len(self.tops) > 1 else self.tops[0]
        if top is None:
            raise ConnectionError("down")
        return SimpleNamespace(
            dialogs=[
                SimpleNamespace(peer=types.PeerChannel(SOURCE), top_message=40),
                SimpleNamespace(peer=types.PeerChannel(DISCUSSION), top_message=top),
            ]
        )

    async def __call__(self, request):
        return await self._call(None, request)


def watch(
    tmp_path,
    monkeypatch,
    capsys,
    discussion_tops,
    *,
    busy_source=False,
    flood=None,
):
    """Run the script for a few fast ticks; return the client, syncs, stdout.

    ``busy_source``: every sync is followed by a pushed post in the source,
    so the loop never waits a whole tick. ``flood``: every sync exits 5 asking
    for that many seconds.
    """
    config = tmp_path / "config.toml"
    config.write_text(CONFIG)
    monkeypatch.setenv("TGCLI_CONFIG", str(config))
    monkeypatch.setenv("TGCLI_READONLY", "0")
    client = WatchClient(discussion_tops)
    make_session_fake(monkeypatch, client)
    syncs = []

    async def fake_exec(*argv, **kwargs):
        syncs.append(argv)
        if busy_source:
            post = SimpleNamespace(chat_id=-(1000000000000 + SOURCE), id=41)
            await client.handler(post)
        done = {"sync": {"copied": 1, "cursor": 40, "discussion_cursor": 9}}
        if flood is not None:
            done = {"error": {"code": "FLOOD_WAIT", "retry_after": flood}}

        async def communicate():
            return json.dumps(done).encode(), b""

        return SimpleNamespace(
            communicate=communicate, returncode=0 if flood is None else 5
        )

    real_wait_for, real_sleep = asyncio.wait_for, asyncio.sleep
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(
        asyncio, "wait_for", lambda aw, timeout: real_wait_for(aw, timeout / 3000)
    )
    monkeypatch.setattr(asyncio, "sleep", lambda seconds: real_sleep(0))

    assert main(["run", SCRIPT, f"-{1000000000000 + SOURCE}", "--run-for", "0.1"]) == 0
    return client, syncs, capsys.readouterr().out


def test_a_comment_the_poll_finds_triggers_a_sync(tmp_path, monkeypatch, capsys):
    client, syncs, out = watch(tmp_path, monkeypatch, capsys, [9, 10])

    assert out.count("sync (start)") == 1
    assert out.count("sync (new message)") == 1
    assert f"new message -{1000000000000 + DISCUSSION} 10" in out
    assert [argv[-3:] for argv in syncs] == [
        ("clone", "sync", f"-{1000000000000 + SOURCE}")
    ] * 2
    polls = [r for r in client.sent if not hasattr(r, "channel")]
    assert polls[0] == functions.messages.GetPeerDialogsRequest(
        [
            types.InputDialogPeer(types.InputPeerChannel(SOURCE, access_hash=5)),
            types.InputDialogPeer(types.InputPeerChannel(DISCUSSION, access_hash=5)),
        ]
    )


def test_no_sync_starts_while_telegrams_flood_wait_runs(tmp_path, monkeypatch, capsys):
    """tgcli keeps no flood state: the caller is what honors `retry_after`."""
    _, syncs, out = watch(tmp_path, monkeypatch, capsys, [9, 10], flood=600)

    assert len(syncs) == 1
    assert "sync (start): exit 5" in out
    assert "sync (new message): skipped, flood wait outlasts this run" in out


def test_an_unchanged_chat_and_a_failed_poll_trigger_nothing(
    tmp_path, monkeypatch, capsys
):
    _, syncs, out = watch(tmp_path, monkeypatch, capsys, [9, 9, 9, None])

    assert len(syncs) == 1
    assert "sync (new message)" not in out
    assert "poll failed: ConnectionError down" in out
    assert out.rstrip().endswith("restart")


def test_a_busy_source_does_not_starve_the_poll(tmp_path, monkeypatch, capsys):
    _, _, out = watch(tmp_path, monkeypatch, capsys, [9, 10], busy_source=True)

    assert f"new message -{1000000000000 + DISCUSSION} 10" in out


def test_a_poll_that_recovers_after_a_failed_baseline_syncs(
    tmp_path, monkeypatch, capsys
):
    _, syncs, out = watch(tmp_path, monkeypatch, capsys, [None, 9])

    assert "poll failed: ConnectionError down" in out
    assert out.count("sync (new message)") == 1
    assert len(syncs) == 2
