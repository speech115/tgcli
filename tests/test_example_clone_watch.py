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
        self.tops = iter(discussion_tops)
        self.sent = []

    async def get_entity(self, ref):
        return types.PeerChannel(SOURCE)

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(peer.channel_id, access_hash=5)

    def on(self, event):
        return lambda handler: handler

    async def _call(self, sender, request, ordered=False):
        self.sent.append(request)
        if isinstance(request, functions.channels.GetFullChannelRequest):
            return SimpleNamespace(full_chat=SimpleNamespace(linked_chat_id=DISCUSSION))
        top = next(self.tops, None)
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


def watch(tmp_path, monkeypatch, capsys, discussion_tops):
    """Run the script for a few fast ticks; return the client, syncs, stdout."""
    config = tmp_path / "config.toml"
    config.write_text(CONFIG)
    monkeypatch.setenv("TGCLI_CONFIG", str(config))
    monkeypatch.setenv("TGCLI_READONLY", "0")
    client = WatchClient(discussion_tops)
    make_session_fake(monkeypatch, client)
    syncs = []

    async def fake_exec(*argv, **kwargs):
        syncs.append(argv)
        done = {"sync": {"copied": 1, "cursor": 40, "discussion_cursor": 9}}

        async def communicate():
            return json.dumps(done).encode(), b""

        return SimpleNamespace(communicate=communicate, returncode=0)

    real_wait_for, real_sleep = asyncio.wait_for, asyncio.sleep
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(
        asyncio, "wait_for", lambda aw, timeout: real_wait_for(aw, timeout / 3000)
    )
    monkeypatch.setattr(asyncio, "sleep", lambda seconds: real_sleep(0))

    assert main(["run", SCRIPT, f"-{1000000000000 + SOURCE}", "--run-for", "0.1"]) == 0
    return client, syncs, capsys.readouterr().out


def test_a_comment_the_poll_finds_triggers_a_sync(tmp_path, monkeypatch, capsys):
    client, syncs, out = watch(tmp_path, monkeypatch, capsys, [9, 10] + [10] * 50)

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


def test_an_unchanged_chat_and_a_failed_poll_trigger_nothing(
    tmp_path, monkeypatch, capsys
):
    _, syncs, out = watch(tmp_path, monkeypatch, capsys, [9, 9, 9])

    assert len(syncs) == 1
    assert "sync (new message)" not in out
    assert "poll failed: ConnectionError down" in out
    assert out.rstrip().endswith("restart")
