import asyncio
import fcntl
from pathlib import Path

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions

from tgcli import session
from tgcli.config import Account
from tgcli.errors import ConfigError

ACCOUNT = Account(alias="t", api_id=1, api_hash="h", session="t")


class FakeTelethonClient:
    def __init__(self, authorized=True):
        self.authorized = authorized
        self.connected = False

    async def connect(self):
        self.connected = True

    async def is_user_authorized(self):
        return self.authorized

    async def disconnect(self):
        self.connected = False


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    return tmp_path


async def test_client_connects_and_disconnects(state, monkeypatch):
    fake = FakeTelethonClient()
    monkeypatch.setattr(
        session, "_make_client", lambda path, account, *, mutation_safe=False: fake
    )
    async with session.client(ACCOUNT) as tg:
        assert tg.connected is True
    assert fake.connected is False


async def test_busy_lock_fails_fast_with_config_error(state, monkeypatch):
    fake = FakeTelethonClient()
    monkeypatch.setattr(
        session, "_make_client", lambda path, account, *, mutation_safe=False: fake
    )
    lock_path = session.session_path(ACCOUNT).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock_path, "w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(ConfigError, match="busy"):
        async with session.client(ACCOUNT):
            pass
    holder.close()


async def test_unauthorized_session_raises_config_error(state, monkeypatch):
    fake = FakeTelethonClient(authorized=False)
    monkeypatch.setattr(
        session, "_make_client", lambda path, account, *, mutation_safe=False: fake
    )
    with pytest.raises(ConfigError, match="not authorized"):
        async with session.client(ACCOUNT):
            pass
    assert fake.connected is False


async def test_revoked_session_raises_reauthentication_error(state, monkeypatch):
    class RevokedClient(FakeTelethonClient):
        async def connect(self):
            raise telethon_errors.SessionRevokedError(request=None)

    monkeypatch.setattr(
        session,
        "_make_client",
        lambda path, account, *, mutation_safe=False: RevokedClient(),
    )

    with pytest.raises(ConfigError, match="needs reauthentication"):
        async with session.client(ACCOUNT):
            pass


async def test_revoked_session_during_command_raises_reauthentication_error(
    state, monkeypatch
):
    fake = FakeTelethonClient()
    monkeypatch.setattr(
        session, "_make_client", lambda path, account, *, mutation_safe=False: fake
    )

    with pytest.raises(ConfigError, match="needs reauthentication"):
        async with session.client(ACCOUNT):
            raise telethon_errors.SessionRevokedError(request=None)


class FailingSender:
    def __init__(self, error_factory):
        self.error_factory = error_factory
        self.calls = 0

    def send(self, request, *, ordered=False):
        del ordered
        self.calls += 1
        future = asyncio.get_running_loop().create_future()
        future.set_exception(self.error_factory(request))
        return future


async def test_mutation_safe_telethon_client_sends_ambiguous_timeout_once(
    tmp_path, monkeypatch
):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("telethon.client.users.asyncio.sleep", fake_sleep)
    tg = session._make_client(
        Path(tmp_path / "mutation-safe"), ACCOUNT, mutation_safe=True
    )
    request = functions.channels.CreateChannelRequest(
        title="marker", about="", broadcast=True, megagroup=False
    )
    sender = FailingSender(
        lambda sent: telethon_errors.TimedOutError(
            request=sent, message="TIMEOUT"
        )
    )

    with pytest.raises(ValueError, match="unsuccessful 1 time"):
        await tg._call(sender, request)

    assert sender.calls == 1
    assert tg._request_retries == 0
    assert sleeps == [2]


async def test_mutation_safe_telethon_client_surfaces_short_flood_wait_without_sleep(
    tmp_path, monkeypatch
):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("telethon.client.users.asyncio.sleep", fake_sleep)
    tg = session._make_client(
        Path(tmp_path / "mutation-safe"), ACCOUNT, mutation_safe=True
    )
    request = functions.channels.CreateChannelRequest(
        title="marker", about="", broadcast=True, megagroup=False
    )
    sender = FailingSender(
        lambda sent: telethon_errors.FloodWaitError(request=sent, capture=5)
    )

    with pytest.raises(telethon_errors.FloodWaitError) as raised:
        await tg._call(sender, request)

    assert raised.value.seconds == 5
    assert sender.calls == 1
    assert tg.flood_sleep_threshold == 0
    assert sleeps == []


def test_regular_telethon_client_keeps_read_retry_defaults(tmp_path):
    tg = session._make_client(Path(tmp_path / "regular"), ACCOUNT)

    assert tg._request_retries == 5
    assert tg.flood_sleep_threshold == 60
