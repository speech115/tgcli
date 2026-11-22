"""What opening a session costs in Telegram requests.

Telethon's `connect()` on a session without saved update state logs in its
updates machinery: users.GetUsers, then updates.GetState and
updates.GetDifference. tgcli never handles live updates, so a command pays
only the layer init and one GetUsers that proves the session is authorized
(and answers the command's own `get_me()`).
"""

from __future__ import annotations

import asyncio
import datetime
import json

import pytest
from telethon.tl import functions, types

from tgcli import session
from tgcli.cli import main
from tgcli.config import Account

ACCOUNT = Account(alias="a", api_id=1, api_hash="h", session="s")


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    return tmp_path


NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
ME = types.User(id=42, access_hash=420, is_self=True, first_name="Me")


class RecordingSender:
    """Answers the requests a session's startup sends; records their types."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.connected = False
        self.auth_key = None
        self.disconnected: asyncio.Future | None = None

    async def connect(self, connection) -> bool:
        del connection
        self.connected = True
        self.disconnected = asyncio.get_running_loop().create_future()
        return True

    def is_connected(self) -> bool:
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False
        if self.disconnected is not None and not self.disconnected.done():
            self.disconnected.set_result(None)

    def send(self, request, *, ordered=False):
        del ordered
        while isinstance(
            request,
            functions.InvokeWithoutUpdatesRequest | functions.InvokeWithLayerRequest,
        ):
            request = request.query
        if isinstance(request, functions.InitConnectionRequest):
            request = request.query
        self.sent.append(type(request).__name__)
        future = asyncio.get_running_loop().create_future()
        future.set_result(_answer(request))
        return future


def _answer(request):
    if isinstance(request, functions.users.GetUsersRequest):
        return [ME]
    if isinstance(request, functions.updates.GetStateRequest):
        return types.updates.State(pts=1, qts=0, date=NOW, seq=1, unread_count=0)
    if isinstance(request, functions.updates.GetDifferenceRequest):
        return types.updates.DifferenceEmpty(date=NOW, seq=1)
    if isinstance(request, functions.updates.GetChannelDifferenceRequest):
        return types.updates.ChannelDifferenceEmpty(pts=10, final=True)
    return None


def _install(monkeypatch) -> RecordingSender:
    sender = RecordingSender()
    real = session._make_client

    def make(path, account, *, mutation_safe=False):
        tg = real(path, account, mutation_safe=mutation_safe)
        tg._sender = sender
        return tg

    monkeypatch.setattr(session, "_make_client", make)
    return sender


async def _open(monkeypatch) -> tuple[RecordingSender, list]:
    sender = _install(monkeypatch)
    seen = []
    async with session.client(ACCOUNT) as tg:
        seen.append((await tg.get_me()).id)
        seen.append((await tg.get_me(input_peer=True)).user_id)
        seen.append((await tg.get_me()).id)
    return sender, seen


async def test_opening_a_session_sends_only_init_and_one_get_users(state, monkeypatch):
    sender, seen = await _open(monkeypatch)

    assert sender.sent == ["GetConfigRequest", "GetUsersRequest"]
    assert seen == [42, 42, 42]


async def test_updates_riding_on_results_fetch_no_difference(state, monkeypatch):
    """Sends, deletes and read marks return Updates that Telethon tracks per
    channel; a channel it marks as behind costs a GetChannelDifference."""
    sender = _install(monkeypatch)
    async with session.client(ACCOUNT) as tg:
        channel = types.Channel(
            id=7,
            title="C",
            photo=types.ChatPhotoEmpty(),
            date=NOW,
            access_hash=70,
            broadcast=True,
        )
        deleted = types.UpdateDeleteChannelMessages(
            channel_id=7, messages=[1], pts=10, pts_count=1
        )
        for update in (deleted, types.UpdateChannelTooLong(channel_id=7)):
            tg._updates_queue.put_nowait(
                types.Updates(
                    updates=[update], users=[], chats=[channel], date=NOW, seq=0
                )
            )
        tg._updates_queue.put_nowait(
            types.Updates(updates=[], users=[], chats=[], date=NOW, seq=99)
        )
        tg._updates_queue.put_nowait(types.UpdatesTooLong())
        for _ in range(20):
            await asyncio.sleep(0)
        assert tg._updates_queue.empty()
        assert tg.is_connected()
        await tg(functions.help.GetNearestDcRequest())

    assert sender.sent == [
        "GetConfigRequest",
        "GetUsersRequest",
        "GetNearestDcRequest",
    ]


async def test_a_held_session_fetches_get_me_once_per_command(state, monkeypatch):
    sender = _install(monkeypatch)
    async with session.hold(ACCOUNT):
        for _ in range(2):
            async with session.client(ACCOUNT) as tg:
                await tg.get_me()
                await tg.get_me()

    assert sender.sent == ["GetConfigRequest"] + ["GetUsersRequest"] * 3


def test_cli_doctor_connect_sends_only_init_and_one_get_users(
    state, tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'default = "a"\n\n[accounts.a]\napi_id = 1\napi_hash = "h"\nsession = "s"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TGCLI_CONFIG", str(config_path))
    session_file = state / "sessions" / "s.session"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.touch()
    session_file.chmod(0o600)
    sender = _install(monkeypatch)

    exit_code = main(["--json", "doctor", "--connect"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["accounts"][0]["checks"]["authorized"] is True
    assert payload["accounts"][0]["user"]["id"] == 42
    assert sender.sent == ["GetConfigRequest", "GetUsersRequest"]
