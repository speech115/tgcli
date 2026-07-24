"""Tests for the unauthorized Telethon client seam."""

from __future__ import annotations

import fcntl

import pytest

from tgcli import authclient
from tgcli.config import Account
from tgcli.errors import ConfigError


class _FakeTg:
    def __init__(self, *, authorized=True, raise_on_connect=None, revoke=False):
        self.authorized = authorized
        self.raise_on_connect = raise_on_connect
        self.revoke = revoke
        self.connected = False
        self.disconnected = False
        self.disconnect_calls = 0

    async def connect(self):
        if self.raise_on_connect is not None:
            raise self.raise_on_connect
        self.connected = True

    def is_connected(self):
        return self.connected

    async def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False
        self.disconnected = True

    async def is_user_authorized(self):
        if self.revoke:
            from telethon.errors import SessionRevokedError

            raise SessionRevokedError(request=None)
        return self.authorized


@pytest.mark.asyncio
async def test_unauthorized_client_takes_and_releases_lock(tmp_path, monkeypatch):
    path = tmp_path / "staged.session"
    fake = _FakeTg()
    monkeypatch.setattr(authclient, "TelegramClient", lambda *a, **k: fake)

    async with authclient.unauthorized_client(path, 1, "hash") as client:
        assert client is fake
        assert fake.connected
        lock = path.with_suffix(".lock")
        assert lock.exists()
        with pytest.raises(BlockingIOError):
            handle = lock.open("w")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                handle.close()

    assert fake.disconnected
    # Lock released — we can take it now.
    handle = path.with_suffix(".lock").open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_unauthorized_client_sets_same_telegram_device_identity(
    tmp_path, monkeypatch
):
    path = tmp_path / "staged.session"
    fake = _FakeTg()
    captured = {}

    def fake_client(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return fake

    monkeypatch.setattr(authclient, "TelegramClient", fake_client)
    monkeypatch.setattr(
        authclient,
        "client_identity",
        lambda: ("tgcli", "TestOS", "1.2.0"),
    )

    async with authclient.unauthorized_client(path, 1, "hash"):
        pass

    assert captured == {
        "args": (str(path), 1, "hash"),
        "kwargs": {
            "device_model": "tgcli",
            "system_version": "TestOS",
            "app_version": "1.2.0",
        },
    }


@pytest.mark.asyncio
async def test_unauthorized_client_busy_lock_raises(tmp_path, monkeypatch):
    path = tmp_path / "staged.session"
    lock = path.with_suffix(".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(authclient, "TelegramClient", lambda *a, **k: _FakeTg())
    try:
        with pytest.raises(ConfigError, match="busy"):
            async with authclient.unauthorized_client(path, 1, "hash"):
                pass
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


@pytest.mark.asyncio
async def test_unauthorized_client_disconnects_on_body_error(tmp_path, monkeypatch):
    path = tmp_path / "staged.session"
    fake = _FakeTg()
    monkeypatch.setattr(authclient, "TelegramClient", lambda *a, **k: fake)

    with pytest.raises(RuntimeError, match="boom"):
        async with authclient.unauthorized_client(path, 1, "hash"):
            raise RuntimeError("boom")

    assert fake.disconnected


@pytest.mark.asyncio
async def test_unauthorized_client_skips_disconnect_if_already_closed(
    tmp_path, monkeypatch
):
    path = tmp_path / "staged.session"
    fake = _FakeTg()
    monkeypatch.setattr(authclient, "TelegramClient", lambda *a, **k: fake)

    async with authclient.unauthorized_client(path, 1, "hash") as client:
        await client.disconnect()

    assert fake.disconnect_calls == 1
    assert fake.disconnected


@pytest.mark.asyncio
async def test_probe_authorized_maps_revoked_to_false(tmp_path, monkeypatch):
    account = Account(alias="main", api_id=1, api_hash="h", session="main")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "main.session").write_bytes(b"x")
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    fake = _FakeTg(revoke=True)
    monkeypatch.setattr(authclient, "TelegramClient", lambda *a, **k: fake)

    assert await authclient.probe_authorized(account) is False
    assert fake.disconnected
