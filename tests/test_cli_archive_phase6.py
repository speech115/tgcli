"""`tg archive refresh` Phase 6 boundary and failure-state tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from telethon.tl.types import User

from tests.conftest import FakeClient, make_session_fake
from tgcli import desktop
from tgcli.archive import refresh as refresh_mod, store as store_mod
from tgcli.cli import main
from tgcli.commands import archive as archive_cmd
from tgcli.errors import PartialFailure

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
session = "main"
"""


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))
    state = tmp_path / "state"
    monkeypatch.setenv("TGCLI_STATE_DIR", str(state))
    (state / "sessions").mkdir(parents=True, exist_ok=True)
    (state / "sessions" / "main.session").write_bytes(b"x")
    return state


def _me():
    return User(
        id=42,
        is_self=True,
        access_hash=1,
        first_name="Me",
        username="me",
        phone="100",
    )


def _client():
    return FakeClient(me=_me())


def _connection(tmp_path: Path):
    conn = store_mod.connect(tmp_path / "archive.db")
    store_mod.ensure_meta(conn, account_user_id=42, account_alias="main")
    return conn


def test_refresh_validates_all_bounded_caps(config_env, capsys):
    cases = (
        ("--max-events", "0", "max-events"),
        ("--max-dialogs", "0", "max-dialogs"),
        ("--max-media", "0", "max-media"),
        ("--transcribe-limit", "0", "transcribe-limit"),
        ("--max-attempts", "0", "max-attempts"),
    )
    for flag, value, needle in cases:
        assert main(["archive", "refresh", flag, value, "--json"]) == 2
        assert needle in capsys.readouterr().err.lower()


def test_refresh_is_readonly_gated(config_env, capsys):
    assert main(["--readonly", "archive", "refresh", "--json"]) == 2
    assert "readonly" in capsys.readouterr().err.lower()


def test_refresh_dispatches_caps_and_returns_composed_result(
    config_env, monkeypatch, capsys
):
    client = _client()
    make_session_fake(monkeypatch, client)
    seen = {}

    async def fake_refresh(tg, alias, **kwargs):
        seen.update(kwargs)
        return {
            "account": {"alias": alias, "user_id": 42},
            "sync": {"applied": {"events": 1}, "media": {"downloaded": 2}},
            "transcribe": {"transcribed": 3, "remaining": False},
            "refresh": {"failure_streak": 0, "notification_sent": False},
        }

    monkeypatch.setattr(archive_cmd, "refresh", fake_refresh)
    assert (
        main(
            [
                "archive",
                "refresh",
                "--max-events",
                "11",
                "--max-dialogs",
                "12",
                "--max-media",
                "13",
                "--transcribe-limit",
                "14",
                "--max-attempts",
                "4",
                "--json",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["sync"]["applied"]["events"] == 1
    assert seen == {
        "max_events": 11,
        "max_dialogs": 12,
        "max_media": 13,
        "transcribe_limit": 14,
        "max_attempts": 4,
    }


@pytest.mark.asyncio
async def test_refresh_runs_sync_then_transcribe_and_resets_failure_state(
    tmp_path, monkeypatch
):
    conn = _connection(tmp_path)
    try:
        refresh_mod.record_refresh_failure(conn, error="old failure")
        order = []
        budgets = []

        async def fake_sync(*_args, budget=None, **_kwargs):
            order.append("sync")
            budgets.append(budget)
            return {"media": {"failed": []}, "applied": {"events": 2}}

        def fake_transcribe(*_args, **_kwargs):
            order.append("transcribe")
            return {"errors": [], "transcribed": 1, "remaining": False}

        monkeypatch.setattr(refresh_mod.sync_mod, "sync_archive", fake_sync)
        monkeypatch.setattr(refresh_mod.transcribe_mod, "run_queue", fake_transcribe)

        data = await refresh_mod.run(
            object(),
            conn,
            account_alias="main",
            account_user_id=42,
            account_dir=tmp_path,
            max_events=5,
            max_dialogs=6,
            max_media=7,
            transcribe_limit=8,
            max_attempts=3,
        )

        assert order == ["sync", "transcribe"]
        assert len(budgets) == 1
        assert data["sync"]["applied"]["events"] == 2
        assert data["transcribe"]["transcribed"] == 1
        state = store_mod.read_account_sync(conn)
        assert state["refresh_failure_streak"] == 0
        assert state["refresh_last_error"] is None
        assert state["refresh_notification_sent"] is False
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_refresh_records_consecutive_failures_and_notifies_once(
    tmp_path, monkeypatch
):
    conn = _connection(tmp_path)
    notifications = []
    monkeypatch.setattr(
        refresh_mod.sync_mod,
        "sync_archive",
        _raise_refresh_error,
    )
    monkeypatch.setattr(
        desktop,
        "notify",
        lambda title, message: notifications.append((title, message)) or True,
    )
    try:
        for _ in range(4):
            with pytest.raises(RuntimeError, match="Telegram unavailable"):
                await refresh_mod.run(
                    object(),
                    conn,
                    account_alias="main",
                    account_user_id=42,
                    account_dir=tmp_path,
                    max_events=5,
                    max_dialogs=6,
                    max_media=7,
                    transcribe_limit=8,
                    max_attempts=3,
                )
        state = store_mod.read_account_sync(conn)
        assert state["refresh_failure_streak"] == 4
        assert state["refresh_last_error"] == "RuntimeError:Telegram unavailable"
        assert state["refresh_notification_sent"] is True
        assert len(notifications) == 1
        assert "status" in notifications[0][1]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_refresh_item_failure_returns_stage_data_and_increments_streak(
    tmp_path, monkeypatch
):
    conn = _connection(tmp_path)
    try:

        async def fake_sync(*_args, **_kwargs):
            return {
                "media": {
                    "failed": [{"peer_id": 7, "message_id": 1, "error": "missing"}]
                },
                "applied": {"events": 1},
            }

        monkeypatch.setattr(refresh_mod.sync_mod, "sync_archive", fake_sync)
        monkeypatch.setattr(
            refresh_mod.transcribe_mod,
            "run_queue",
            lambda *_args, **_kwargs: {
                "errors": [],
                "skipped_missing_media": 0,
                "transcribed": 0,
                "remaining": True,
            },
        )
        with pytest.raises(PartialFailure) as caught:
            await refresh_mod.run(
                object(),
                conn,
                account_alias="main",
                account_user_id=42,
                account_dir=tmp_path,
                max_events=5,
                max_dialogs=6,
                max_media=7,
                transcribe_limit=8,
                max_attempts=3,
            )
        assert caught.value.data["sync"]["applied"]["events"] == 1
        assert caught.value.data["refresh"]["failure_streak"] == 1
        assert store_mod.read_account_sync(conn)["refresh_failure_streak"] == 1
    finally:
        conn.close()


async def _raise_refresh_error(*_args, **_kwargs):
    raise RuntimeError("Telegram unavailable")


def test_desktop_notify_is_darwin_only_and_keeps_message_in_osascript(
    monkeypatch,
):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    assert desktop.notify("tgcli", "archive refresh failed; run status") is True
    assert calls[0][:2] == ["osascript", "-e"]
    assert "archive refresh failed" in calls[0][2]
