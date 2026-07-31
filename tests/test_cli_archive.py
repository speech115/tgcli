"""`tg archive` Phase 1 boundary tests (ADR-0068)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from telethon.tl.types import Channel, Message, PeerUser, User

from tests.conftest import FakeClient, make_session_fake
from tgcli.cli import main
from tgcli.commands import archive as archive_cmd

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


def _me(*, user_id: int = 42):
    return User(
        id=user_id,
        is_self=True,
        access_hash=1,
        first_name="Me",
        username="me",
        phone="100",
    )


def _user(*, user_id: int = 7, username: str = "alice"):
    return User(
        id=user_id,
        is_self=False,
        access_hash=2,
        first_name="Alice",
        username=username,
        phone="200",
    )


def _channel(*, channel_id: int = 1234, title: str = "News", megagroup: bool = False):
    return Channel(
        id=channel_id,
        title=title,
        photo=None,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        access_hash=99,
        megagroup=megagroup,
        username="news" if not megagroup else "group",
    )


def _msg(*, mid: int = 1, text: str = "hi", peer=None):
    return Message(
        id=mid,
        peer_id=peer or PeerUser(7),
        message=text,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        out=False,
    )


def _client(*, me=None, entities=None, messages=()):
    return FakeClient(me=me or _me(), entities=entities or {}, messages=list(messages))


def test_archive_requires_subcommand(config_env, capsys):
    assert main(["archive", "--json"]) == 1
    assert "required" in capsys.readouterr().err.lower()


def test_backfill_requires_at_least_one_chat(config_env, capsys):
    assert main(["archive", "backfill", "--json"]) == 1
    err = capsys.readouterr().err.lower()
    assert "chat" in err or "required" in err


def test_backfill_rejects_non_positive_and_over_cap_limit(config_env, capsys):
    assert main(["archive", "backfill", "@alice", "--limit", "0", "--json"]) == 2
    assert "limit" in capsys.readouterr().err.lower()
    over = str(archive_cmd.MAX_BACKFILL_LIMIT + 1)
    assert main(["archive", "backfill", "@alice", "--limit", over, "--json"]) == 2
    assert "limit" in capsys.readouterr().err.lower()


def test_backfill_rejects_too_many_dialogs(config_env, capsys):
    chats = [f"@c{i}" for i in range(archive_cmd.MAX_BACKFILL_DIALOGS + 1)]
    assert main(["archive", "backfill", *chats, "--json"]) == 2
    assert "dialog" in capsys.readouterr().err.lower()


def test_init_creates_store_and_binds_account(config_env, monkeypatch, capsys):
    client = _client()
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["account"]["alias"] == "main"
    assert data["account"]["user_id"] == 42
    assert data["created"] is True
    root = archive_cmd.account_dir("main")
    assert root.is_dir()
    assert root.stat().st_mode & 0o777 == 0o700
    db = root / "archive.db"
    assert db.is_file()
    assert db.stat().st_mode & 0o777 == 0o600


def test_init_idempotent_same_account(config_env, monkeypatch, capsys):
    client = _client()
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "init", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["created"] is False
    assert data["account"]["user_id"] == 42


def test_init_mismatch_is_hard_error(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, _client(me=_me(user_id=42)))
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    make_session_fake(monkeypatch, _client(me=_me(user_id=99)))
    assert main(["archive", "init", "--json"]) == 2
    assert "account" in capsys.readouterr().err.lower()


def test_list_and_status_are_offline(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, _client())
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    from contextlib import asynccontextmanager

    from tgcli import session

    opened = {"n": 0}

    @asynccontextmanager
    async def boom(*_a, **_k):
        opened["n"] += 1
        raise AssertionError("offline archive must not open a Telegram session")
        yield  # pragma: no cover

    monkeypatch.setattr(session, "client", boom)
    assert main(["archive", "list", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["standing"] == {"kind": "private", "description": "private 1:1 dialogs"}
    assert data["explicit"] == []
    assert main(["archive", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["account"]["alias"] == "main"
    assert status["counts"]["messages"] == 0
    assert opened["n"] == 0


def test_add_requires_group_or_channel_and_lists(config_env, monkeypatch, capsys):
    channel = _channel()
    user = _user()
    client = _client(entities={"@news": channel, "@alice": user, 7: user})
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "add", "@alice", "--json"]) == 2
    assert "private" in capsys.readouterr().err.lower()
    assert main(["archive", "add", "@news", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["added"]["kind"] == "channel"
    assert main(["archive", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed["explicit"]) == 1
    assert listed["explicit"][0]["kind"] == "channel"


def test_remove_drops_explicit_scope(config_env, monkeypatch, capsys):
    channel = _channel()
    client = _client(entities={"@news": channel})
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    assert main(["archive", "add", "@news", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "remove", "@news", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["removed"]["kind"] == "channel"
    assert main(["archive", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["explicit"] == []


def test_backfill_private_without_add_and_group_requires_add(
    config_env, monkeypatch, capsys
):
    user = _user()
    channel = _channel()
    messages = [_msg(mid=3, text="c"), _msg(mid=2, text="b"), _msg(mid=1, text="a")]
    client = _client(
        entities={"@alice": user, 7: user, "@news": channel},
        messages=messages,
    )
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "backfill", "@news", "--limit", "2", "--json"]) == 2
    assert "add" in capsys.readouterr().err.lower()
    assert main(["archive", "backfill", "@alice", "--limit", "2", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dialogs"][0]["stored"] == 2
    assert data["dialogs"][0]["chat"] == "@alice"
    assert main(["archive", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["counts"]["messages"] == 2


def test_backfill_channel_after_add_stores_universal_shape(
    config_env, monkeypatch, capsys
):
    channel = _channel()
    messages = [
        _msg(mid=10, text="ten", peer=None),
        _msg(mid=9, text="nine"),
    ]
    # peer_id on Message is unused by message_to_dict; entity comes from resolve.
    client = _client(entities={"@news": channel}, messages=messages)
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    assert main(["archive", "add", "@news", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "backfill", "@news", "--limit", "10", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dialogs"][0]["stored"] == 2
    # Resume stores nothing new when already complete for this window.
    assert main(["archive", "backfill", "@news", "--limit", "10", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dialogs"][0]["stored"] == 0


def test_readonly_blocks_mutating_archive_commands(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, _client())
    assert main(["--readonly", "archive", "init", "--json"]) == 2
    assert "readonly" in capsys.readouterr().err.lower()


def test_readonly_blocks_backfill(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, _client())
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["--readonly", "archive", "backfill", "@alice", "--json"]) == 2
    assert "readonly" in capsys.readouterr().err.lower()


def test_list_and_status_before_init_are_not_found(config_env, capsys):
    assert main(["archive", "list", "--json"]) == 4
    err = json.loads(capsys.readouterr().err)
    assert err["error"]["code"] == "NOT_FOUND"
    assert main(["archive", "status", "--json"]) == 4
    err = json.loads(capsys.readouterr().err)
    assert err["error"]["code"] == "NOT_FOUND"


def test_offline_alias_store_mismatch(config_env, monkeypatch, capsys):
    import sqlite3

    make_session_fake(monkeypatch, _client())
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    conn = sqlite3.connect(archive_cmd.db_path("main"))
    conn.execute("UPDATE meta SET account_alias = 'other'")
    conn.commit()
    conn.close()
    assert main(["archive", "list", "--json"]) == 2
    assert "alias" in capsys.readouterr().err.lower()
    assert main(["archive", "status", "--json"]) == 2
    assert "alias" in capsys.readouterr().err.lower()


def test_backfill_flood_wait_persists_checkpoint_and_exits_5(
    config_env, monkeypatch, capsys
):
    from telethon import errors as telethon_errors

    from tgcli.clone import flood

    user = _user()
    messages = [_msg(mid=3, text="c"), _msg(mid=2, text="b"), _msg(mid=1, text="a")]

    class FloodMidIter(FakeClient):
        async def iter_messages(self, entity, **kwargs):
            self.iter_messages_calls.append(
                (entity, kwargs.get("search"), kwargs.get("limit"))
            )
            yielded = 0
            async for message in super().iter_messages(entity, **kwargs):
                if yielded >= 1:
                    raise telethon_errors.FloodWaitError(request=None, capture=90)
                yielded += 1
                yield message

    client = FloodMidIter(
        me=_me(user_id=42),
        entities={"@alice": user, 7: user},
        messages=messages,
    )
    client._self_id = 42
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "backfill", "@alice", "--limit", "10", "--json"]) == 5
    err = json.loads(capsys.readouterr().err)
    assert err["error"]["code"] == "FLOOD_WAIT"
    assert err["error"]["retry_after"] == 90
    assert flood.cooldown_deadline(42) is not None
    assert main(["archive", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["counts"]["messages"] == 1
    assert status["dialogs"]
    assert status["dialogs"][0]["last_error"].startswith("FLOOD_WAIT:")
    assert status["dialogs"][0]["oldest_id"] == 3
    assert status["dialogs"][0]["newest_id"] == 3


def test_backfill_respects_armed_account_cooldown(config_env, monkeypatch, capsys):
    from datetime import timedelta

    from tgcli.clone import flood

    user = _user()
    client = _client(entities={"@alice": user, 7: user}, messages=[_msg()])
    client._self_id = 42
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    flood.arm_cooldown(42, datetime.now(UTC) + timedelta(minutes=10))
    assert main(["archive", "backfill", "@alice", "--json"]) == 5
    err = json.loads(capsys.readouterr().err)
    assert err["error"]["code"] == "FLOOD_WAIT"
    assert client.iter_messages_calls == []


def test_network_command_verifies_live_account_id(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, _client(me=_me(user_id=42)))
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    channel = _channel()
    make_session_fake(
        monkeypatch, _client(me=_me(user_id=99), entities={"@news": channel})
    )
    assert main(["archive", "add", "@news", "--json"]) == 2
    assert "account" in capsys.readouterr().err.lower()


def test_store_stats_reports_archive_and_cleanup_keeps_it(
    config_env, monkeypatch, capsys
):
    make_session_fake(monkeypatch, _client())
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    root = archive_cmd.account_dir("main")
    marker = root / "keep-me.txt"
    marker.write_text("secret-archive-bytes")
    assert main(["store", "stats", "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["archive"]["bytes"] >= len("secret-archive-bytes")
    assert stats["archive"]["db"]["count"] >= 1
    assert main(["store", "cleanup", "--confirm", "--json"]) == 0
    cleanup = json.loads(capsys.readouterr().out)
    assert cleanup["kept"]["archive"] is True
    assert marker.exists()
    assert (root / "archive.db").exists()


def test_config_archive_root_override(tmp_path, monkeypatch, capsys):
    custom = tmp_path / "custom-archive"
    config = tmp_path / "config.toml"
    config.write_text(SAMPLE + "\n[archive]\n" + f'root = "{custom}"\n')
    monkeypatch.setenv("TGCLI_CONFIG", str(config))
    state = tmp_path / "state"
    (state / "sessions").mkdir(parents=True)
    (state / "sessions" / "main.session").write_bytes(b"x")
    monkeypatch.setenv("TGCLI_STATE_DIR", str(state))
    make_session_fake(monkeypatch, _client())
    assert main(["archive", "init", "--json"]) == 0
    assert (custom / "main" / "archive.db").is_file()
    assert not (state / "archive").exists()
