"""Tests for the unauthorized Telethon client seam."""

from __future__ import annotations

import fcntl

import pytest
from telethon import errors as telethon_errors, functions

from tgcli import authclient, session
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
        self.requests = []

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

    async def __call__(self, request):
        self.requests.append(request)
        if self.revoke:
            raise telethon_errors.SessionRevokedError(request=request)
        if not self.authorized:
            raise telethon_errors.AuthKeyUnregisteredError(request=request)
        return object()


@pytest.mark.asyncio
async def test_unauthorized_client_busy_lock_raises(tmp_path, monkeypatch):
    path = tmp_path / "staged.session"
    lock = path.with_suffix(".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(session, "Client", lambda *a, **k: _FakeTg())
    try:
        with pytest.raises(ConfigError, match="busy"):
            async with authclient.unauthorized_client(path, 1, "hash"):
                pass
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


@pytest.mark.asyncio
async def test_probe_authorized_maps_revoked_to_false(tmp_path, monkeypatch):
    account = Account(alias="main", api_id=1, api_hash="h", session="main")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "main.session").write_bytes(b"x")
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    fake = _FakeTg(revoke=True)
    monkeypatch.setattr(session, "Client", lambda *a, **k: fake)

    assert await authclient.probe_authorized(account) is False
    assert len(fake.requests) == 1
    assert isinstance(fake.requests[0], functions.updates.GetStateRequest)
    assert fake.disconnected
