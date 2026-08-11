"""Telegram-lane scheduler behavior for ADR-0087 slice 2."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tgcli.clone import state as clone_state
from tgcli.commands import (
    archive as archive_cmd,
    archive_jobs as archive_jobs_cmd,
    clone as clone_cmd,
)
from tgcli.config import load_config
from tgcli.errors import PartialFailure, PolicyError, RateLimitError
from tgcli.jobs import runner, store

NOW = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
CONFIG = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
session = "main"
"""


@pytest.fixture
def telegram_registry(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(CONFIG)
    monkeypatch.setenv("TGCLI_CONFIG", str(config_path))
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    conn = store.connect("main")
    try:
        yield conn, load_config()
    finally:
        conn.close()


def _add(conn, key, kind, spec):
    return store.add_job(
        conn,
        key=key,
        kind=kind,
        lane="telegram",
        spec=spec,
        priority="normal",
        replace=False,
        now=NOW,
    )["job"]


class Telegram:
    def __init__(self, user_id=42):
        self.user_id = user_id

    async def get_me(self):
        return SimpleNamespace(id=self.user_id)

    async def get_entity(self, _ref):
        return SimpleNamespace(
            id=111,
            title="Resolved source",
            broadcast=True,
            megagroup=False,
        )


def test_telegram_lane_binds_identity_before_work_and_refuses_mismatch(
    telegram_registry, monkeypatch
):
    conn, config = telegram_registry
    _add(
        conn,
        "sync",
        "archive-sync",
        {"max_events": 10, "max_dialogs": 2, "max_media": 1},
    )

    async def fake_sync(*_args, **_kwargs):
        return {"remaining": False}

    monkeypatch.setattr(archive_cmd, "sync", fake_sync)
    monkeypatch.setattr(runner, "_progress_token", lambda *_args, **_kwargs: {})
    first = asyncio.run(
        runner.run_telegram(
            Telegram(42),
            "main",
            max_runtime=1,
            config=config,
            wall_clock=lambda: NOW,
        )
    )
    assert first["completed"] == 1
    assert store.read_meta(conn)["account_user_id"] == 42

    with pytest.raises(PolicyError, match="user"):
        asyncio.run(
            runner.run_telegram(
                Telegram(99),
                "main",
                max_runtime=1,
                config=config,
                wall_clock=lambda: NOW,
            )
        )


def test_telegram_adapters_use_fixed_quanta(telegram_registry, monkeypatch):
    conn, config = telegram_registry
    _add(
        conn,
        "a-backfill",
        "archive-backfill",
        {"chats": ["@one", "@two"], "limit": 25, "private": False},
    )
    _add(
        conn,
        "b-sync",
        "archive-sync",
        {"max_events": 40, "max_dialogs": 3, "max_media": 2},
    )
    _add(conn, "c-clone", "clone-sync", {"source": "-100123"})
    calls = []

    async def fake_backfill(tg, alias, **kwargs):
        calls.append(("backfill", tg, alias, kwargs))
        return {"remaining": False}

    async def fake_sync(tg, alias, **kwargs):
        assert callable(kwargs.pop("should_stop"))
        calls.append(("sync", tg, alias, kwargs))
        return {"remaining": False}

    async def fake_clone(tg, source, alias, *, limit):
        calls.append(("clone", tg, alias, {"source": source, "limit": limit}))
        return {"remaining": False}

    monkeypatch.setattr(archive_jobs_cmd, "backfill_quantum", fake_backfill)
    monkeypatch.setattr(archive_cmd, "sync", fake_sync)
    monkeypatch.setattr(clone_cmd, "sync_text", fake_clone)
    monkeypatch.setattr(runner, "_progress_token", lambda *_args, **_kwargs: {})

    tg = Telegram()
    result = asyncio.run(
        runner.run_telegram(
            tg,
            "main",
            max_runtime=5,
            config=config,
            wall_clock=lambda: NOW,
        )
    )
    assert result["selected"] == 3
    assert result["completed"] == 3
    assert calls == [
        (
            "backfill",
            tg,
            "main",
            {
                "chats": ["@one", "@two"],
                "limit": 25,
                "private": False,
                "config": config,
            },
        ),
        (
            "sync",
            tg,
            "main",
            {
                "max_events": 40,
                "max_dialogs": 3,
                "max_media": 2,
                "config": config,
            },
        ),
        (
            "clone",
            tg,
            "main",
            {"source": "-100123", "limit": 50},
        ),
    ]


def test_rate_limit_is_deferred_without_failure_streak(telegram_registry, monkeypatch):
    conn, config = telegram_registry
    _add(
        conn,
        "sync",
        "archive-sync",
        {"max_events": 10, "max_dialogs": 2, "max_media": 1},
    )

    async def limited(*_args, **_kwargs):
        raise RateLimitError("cooling", retry_after=120)

    monkeypatch.setattr(archive_cmd, "sync", limited)
    monkeypatch.setattr(runner, "_progress_token", lambda *_args, **_kwargs: {})
    result = asyncio.run(
        runner.run_telegram(
            Telegram(),
            "main",
            max_runtime=1,
            config=config,
            wall_clock=lambda: NOW,
        )
    )
    job = store.show_job(conn, "sync")["job"]
    assert result["queued"] == 1
    assert job["state"] == "queued"
    assert job["failure_streak"] == 0
    assert job["not_before"] == (NOW + timedelta(seconds=120)).isoformat()
    assert job["last_result"] == {
        "remaining": True,
        "retry_after": 120,
        "stop_reason": "cooldown_deferred",
    }


def test_normal_governor_stop_stays_queued_and_ends_the_invocation(
    telegram_registry, monkeypatch
):
    conn, config = telegram_registry
    _add(
        conn,
        "sync",
        "archive-sync",
        {"max_events": 10, "max_dialogs": 2, "max_media": 1},
    )

    async def stopped(*_args, **_kwargs):
        return {
            "remaining": True,
            "stop_reason": "breadth_budget_exhausted",
        }

    monkeypatch.setattr(archive_cmd, "sync", stopped)
    monkeypatch.setattr(runner, "_progress_token", lambda *_args, **_kwargs: {})
    result = asyncio.run(
        runner.run_telegram(
            Telegram(),
            "main",
            max_runtime=1,
            config=config,
            wall_clock=lambda: NOW,
        )
    )
    assert result["queued"] == 1
    assert result["stop_reason"] == "breadth_budget_exhausted"
    assert store.show_job(conn, "sync")["job"]["failure_streak"] == 0


def test_cancel_requested_inside_archive_sync_wins_at_its_boundary(
    telegram_registry, monkeypatch
):
    conn, config = telegram_registry
    _add(
        conn,
        "sync",
        "archive-sync",
        {"max_events": 10, "max_dialogs": 2, "max_media": 1},
    )

    async def cancel_mid_quantum(*_args, **kwargs):
        store.cancel_job(conn, "sync", now=NOW)
        assert kwargs["should_stop"]() is True
        return {"remaining": True}

    monkeypatch.setattr(archive_cmd, "sync", cancel_mid_quantum)
    monkeypatch.setattr(runner, "_progress_token", lambda *_args, **_kwargs: {})
    result = asyncio.run(
        runner.run_telegram(
            Telegram(),
            "main",
            max_runtime=1,
            config=config,
            wall_clock=lambda: NOW,
        )
    )
    assert result["cancelled"] == 1
    assert store.show_job(conn, "sync")["job"]["state"] == "cancelled"


def test_progress_probe_failure_does_not_leave_job_running(
    telegram_registry, monkeypatch
):
    conn, config = telegram_registry
    _add(conn, "clone", "clone-sync", {"source": "-100123"})
    calls = 0

    def progress(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"cursor": 1}
        raise RuntimeError("progress unavailable")

    async def broken(*_args, **_kwargs):
        raise RuntimeError("work failed")

    monkeypatch.setattr(runner, "_progress_token", progress)
    monkeypatch.setattr(clone_cmd, "sync_text", broken)
    asyncio.run(
        runner.run_telegram(
            Telegram(),
            "main",
            max_runtime=1,
            config=config,
            wall_clock=lambda: NOW,
        )
    )
    job = store.show_job(conn, "clone")["job"]
    assert job["state"] == "queued"
    assert job["failure_streak"] == 1


def test_progress_before_runtime_error_requeues_and_resets_streak(
    telegram_registry, monkeypatch
):
    conn, config = telegram_registry
    _add(conn, "clone", "clone-sync", {"source": "-100123"})
    tokens = iter([{"cursor": 1}, {"cursor": 2}])
    monkeypatch.setattr(
        runner, "_progress_token", lambda *_args, **_kwargs: next(tokens)
    )

    async def broken(*_args, **_kwargs):
        raise RuntimeError("after checkpoint")

    monkeypatch.setattr(clone_cmd, "sync_text", broken)
    result = asyncio.run(
        runner.run_telegram(
            Telegram(),
            "main",
            max_runtime=1,
            config=config,
            wall_clock=lambda: NOW,
        )
    )
    job = store.show_job(conn, "clone")["job"]
    assert result["queued"] == 1
    assert job["failure_streak"] == 0
    assert job["last_result"] == {
        "error": {"code": "RUNTIME", "message": "after checkpoint"},
        "progress": {"cursor": 2},
        "remaining": True,
    }


def test_unrelated_clone_progress_does_not_reset_failure_streak(
    telegram_registry, monkeypatch
):
    conn, config = telegram_registry
    selected = clone_state.CloneState.new(
        account_user_id=42,
        source_peer_id=111,
        source_title="Selected",
    )
    unrelated = clone_state.CloneState.new(
        account_user_id=42,
        source_peer_id=222,
        source_title="Unrelated",
    )
    clone_state.save(selected)
    clone_state.save(unrelated)
    _add(conn, "clone", "clone-sync", {"source": "111"})

    async def broken(*_args, **_kwargs):
        changed = clone_state.load(unrelated.clone_id)
        assert changed is not None
        changed.cursor = 1
        clone_state.save(changed)
        raise RuntimeError("selected clone failed")

    monkeypatch.setattr(clone_cmd, "sync_text", broken)
    asyncio.run(
        runner.run_telegram(
            Telegram(),
            "main",
            max_runtime=1,
            config=config,
            wall_clock=lambda: NOW,
        )
    )

    job = store.show_job(conn, "clone")["job"]
    assert job["state"] == "queued"
    assert job["failure_streak"] == 1


def test_username_clone_progress_uses_resolved_identity(telegram_registry, monkeypatch):
    conn, config = telegram_registry
    selected = clone_state.CloneState.new(
        account_user_id=42,
        source_peer_id=111,
        source_title="Title unrelated to username",
    )
    clone_state.save(selected)
    _add(conn, "clone", "clone-sync", {"source": "@selected"})

    async def broken(*_args, **_kwargs):
        changed = clone_state.load(selected.clone_id)
        assert changed is not None
        changed.cursor = 1
        clone_state.save(changed)
        raise RuntimeError("after selected checkpoint")

    monkeypatch.setattr(clone_cmd, "sync_text", broken)
    asyncio.run(
        runner.run_telegram(
            Telegram(),
            "main",
            max_runtime=1,
            config=config,
            wall_clock=lambda: NOW,
        )
    )

    job = store.show_job(conn, "clone")["job"]
    assert job["state"] == "queued"
    assert job["failure_streak"] == 0


def test_policy_partial_failure_is_terminal_even_after_progress(
    telegram_registry, monkeypatch
):
    conn, config = telegram_registry
    _add(conn, "clone", "clone-sync", {"source": "-100123"})
    monkeypatch.setattr(runner, "_progress_token", lambda *_args, **_kwargs: {})

    async def degraded(*_args, **_kwargs):
        raise PartialFailure(
            "degraded",
            {"remaining": True},
            cause=PolicyError("quote fallback"),
        )

    monkeypatch.setattr(clone_cmd, "sync_text", degraded)
    result = asyncio.run(
        runner.run_telegram(
            Telegram(),
            "main",
            max_runtime=1,
            config=config,
            wall_clock=lambda: NOW,
        )
    )
    job = store.show_job(conn, "clone")["job"]
    assert result["failed"] == 1
    assert job["state"] == "failed"
    assert job["last_error"] == {"code": "BLOCKED", "message": "quote fallback"}
