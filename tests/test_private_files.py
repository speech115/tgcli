"""Secret state files are 0600 from the moment they exist.

`chmod` is disabled here, so a file created loose and tightened afterwards
fails: only the mode the file was created with counts.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tgcli import authclient, invocations, safety, session, state_paths
from tgcli.archive import schema
from tgcli.clone import statedb
from tgcli.commands import accounts as accounts_cmd
from tgcli.config import Account


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


@pytest.fixture
def no_chmod(monkeypatch, wide_umask, tmp_path):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: None)


class _FakeTg:
    async def connect(self):
        pass

    async def is_user_authorized(self):
        return True

    def is_connected(self):
        return False

    async def disconnect(self):
        pass


def test_audit_log_is_created_0600(no_chmod):
    safety.append_audit("send", "main", {"preview_id": "p_test"})

    assert _mode(safety.audit_path()) == 0o600


def test_invocation_journal_is_created_0600(no_chmod):
    invocations.log_invocation(command="send", exit_code=0, duration_ms=1)

    assert _mode(state_paths.state_dir() / "invocations.jsonl") == 0o600


def test_new_config_is_created_0600(no_chmod, tmp_path):
    config = tmp_path / "config" / "config.toml"

    accounts_cmd._append_config_block(config, "fresh", 1, "h")

    assert _mode(config) == 0o600


@pytest.mark.asyncio
async def test_session_file_exists_at_0600_before_telethon_opens_it(
    no_chmod, monkeypatch
):
    seen = []

    def make_client(path, account, *, mutation_safe=False):
        seen.append(_mode(path))
        return _FakeTg()

    monkeypatch.setattr(session, "_make_client", make_client)
    account = Account(alias="t", api_id=1, api_hash="h", session="t")

    async with session.client(account):
        pass

    assert seen == [0o600]


@pytest.mark.asyncio
async def test_login_session_file_exists_at_0600_before_telethon_opens_it(
    no_chmod, monkeypatch, tmp_path
):
    path = tmp_path / "staged.session"
    seen = []

    def build_client(path, api_id, api_hash, **extra):
        seen.append(_mode(path))
        return _FakeTg()

    monkeypatch.setattr(authclient, "build_client", build_client)

    async with authclient.unauthorized_client(path, 1, "hash"):
        pass

    assert seen == [0o600]


def test_archive_store_and_sidecars_are_created_0600(no_chmod, tmp_path):
    path = tmp_path / "archive.db"

    conn = schema.connect(path)
    try:
        modes = [_mode(p) for p in (path, *statedb.sidecar_paths(path))]
    finally:
        conn.close()

    assert modes == [0o600] * 3


def test_clone_state_and_sidecars_are_created_0600(no_chmod, tmp_path):
    path = tmp_path / "clone.db"

    conn = statedb.connect(path, create=True)
    try:
        modes = [_mode(p) for p in (path, *statedb.sidecar_paths(path))]
    finally:
        conn.close()

    assert modes == [0o600] * 3
