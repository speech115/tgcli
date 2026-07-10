import fcntl

import pytest
from telethon import errors as telethon_errors

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
    monkeypatch.setattr(session, "_make_client", lambda path, account: fake)
    async with session.client(ACCOUNT) as tg:
        assert tg.connected is True
    assert fake.connected is False


async def test_busy_lock_fails_fast_with_config_error(state, monkeypatch):
    fake = FakeTelethonClient()
    monkeypatch.setattr(session, "_make_client", lambda path, account: fake)
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
    monkeypatch.setattr(session, "_make_client", lambda path, account: fake)
    with pytest.raises(ConfigError, match="not authorized"):
        async with session.client(ACCOUNT):
            pass
    assert fake.connected is False


async def test_revoked_session_raises_reauthentication_error(state, monkeypatch):
    class RevokedClient(FakeTelethonClient):
        async def connect(self):
            raise telethon_errors.SessionRevokedError(request=None)

    monkeypatch.setattr(session, "_make_client", lambda path, account: RevokedClient())

    with pytest.raises(ConfigError, match="needs reauthentication"):
        async with session.client(ACCOUNT):
            pass
