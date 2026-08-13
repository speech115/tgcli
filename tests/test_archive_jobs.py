"""Command-owned archive quantum behavior for ADR-0087."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from telethon.tl.types import Message, PeerUser, User

from tests.conftest import FakeClient
from tgcli.archive import private_enum as private_enum_mod, store as archive_store
from tgcli.commands import archive_jobs as archive_jobs_cmd
from tgcli.config import Config


def _user(user_id: int, username: str) -> User:
    return User(
        id=user_id,
        is_self=False,
        access_hash=user_id,
        first_name=username.title(),
        username=username,
        phone=None,
    )


def _dialog(entity: User, *, day: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=entity.id,
        name=entity.first_name or entity.username or "X",
        entity=entity,
        is_user=True,
        is_group=False,
        is_channel=False,
        unread_count=0,
        date=datetime(2026, 1, day, tzinfo=UTC),
        message=SimpleNamespace(id=day),
        dialog=SimpleNamespace(unread_mentions_count=0),
    )


def test_second_private_quantum_does_not_rewalk_completed_head(tmp_path, monkeypatch):
    """T37: durable enum cursor skips the completed head on the next quantum."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    archive_root = tmp_path / "archive"
    config = Config(
        default_account=None,
        accounts={},
        archive_root=archive_root,
    )
    directory = archive_store.ensure_account_dir(archive_root, "main")
    conn = archive_store.connect(archive_store.db_path_for(directory))
    archive_store.ensure_meta(conn, account_user_id=42, account_alias="main")
    completed = [_user(100 + index, f"done{index}") for index in range(5)]
    pending = _user(200, "pending")
    for entity in completed:
        archive_store.upsert_sync_state(
            conn,
            entity.id,
            oldest_id=1,
            newest_id=1,
            more=False,
            kind="user",
            title=entity.first_name,
            username=entity.username,
            chat_ref=f"@{entity.username}",
        )
    conn.commit()
    conn.close()

    dialogs = [_dialog(entity, day=index + 1) for index, entity in enumerate(completed)]
    dialogs.append(_dialog(pending, day=10))
    message = Message(
        id=1,
        peer_id=PeerUser(pending.id),
        message="hello",
        date=datetime(2026, 1, 10, tzinfo=UTC),
        out=False,
    )
    entities = {f"@{entity.username}": entity for entity in [*completed, pending]}
    entities[pending.id] = pending
    client = FakeClient(
        me=User(
            id=42,
            is_self=True,
            access_hash=42,
            first_name="Me",
            username="me",
            phone=None,
        ),
        entities=entities,
        messages=[message],
        dialogs=dialogs,
    )

    first = asyncio.run(
        archive_jobs_cmd.backfill_quantum(
            client,
            "main",
            chats=[],
            private=True,
            limit=10,
            config=config,
        )
    )
    assert first["mode"] == "private"
    assert [item["chat"] for item in first["dialogs"]] == ["@pending"]
    assert first["skipped_complete"] == 5
    assert client.iter_dialogs_calls[0]["offset_peer"] is None

    client.iter_dialogs_calls.clear()
    second = asyncio.run(
        archive_jobs_cmd.backfill_quantum(
            client,
            "main",
            chats=[],
            private=True,
            limit=10,
            config=config,
        )
    )
    assert second["skipped_complete"] == 0
    assert second["dialogs"] == []
    assert client.iter_dialogs_calls, "second quantum must resume iter_dialogs"
    resume = client.iter_dialogs_calls[0]
    assert resume["offset_peer"] is not None
    offset_user = getattr(resume["offset_peer"], "user_id", None)
    # First quantum advanced past the completed head and the dialog it finished.
    assert offset_user == pending.id


def test_private_enum_cursor_does_not_advance_past_incomplete_pending(
    tmp_path, monkeypatch
):
    """Thermos security: max_dialogs lookahead must not skip a still-more peer.

    Enumerate with max_dialogs=2: incomplete A, completed B, incomplete C.
    Persisting B would make the next quantum start after B and permanently
    skip A (ADR-0118 decision 2).
    """
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    archive_root = tmp_path / "archive"
    directory = archive_store.ensure_account_dir(archive_root, "main")
    conn = archive_store.connect(archive_store.db_path_for(directory))
    archive_store.ensure_meta(conn, account_user_id=42, account_alias="main")
    pending_a = _user(201, "pending_a")
    completed_b = _user(202, "done_b")
    pending_c = _user(203, "pending_c")
    archive_store.upsert_sync_state(
        conn,
        completed_b.id,
        oldest_id=1,
        newest_id=1,
        more=False,
        kind="user",
        title=completed_b.first_name,
        username=completed_b.username,
        chat_ref="@done_b",
    )
    # Force last_backfill_at so skip_complete treats B as finished.
    conn.execute(
        "UPDATE sync_state SET last_backfill_at = ? WHERE peer_id = ?",
        (datetime.now(UTC).isoformat(), completed_b.id),
    )
    conn.commit()

    dialogs = [
        _dialog(pending_a, day=1),
        _dialog(completed_b, day=2),
        _dialog(pending_c, day=3),
    ]
    client = FakeClient(
        me=User(
            id=42,
            is_self=True,
            access_hash=42,
            first_name="Me",
            username="me",
            phone=None,
        ),
        entities={
            "@pending_a": pending_a,
            "@done_b": completed_b,
            "@pending_c": pending_c,
            pending_a.id: pending_a,
            completed_b.id: completed_b,
            pending_c.id: pending_c,
        },
        dialogs=dialogs,
    )

    refs, skipped, cursors = asyncio.run(
        private_enum_mod.enumerate_private_dialogs(
            client, conn, max_dialogs=2, skip_complete=True
        )
    )
    assert refs == ["@pending_a", "@pending_c"]
    assert skipped == 1
    assert len(cursors) == 2

    token = archive_store.read_account_sync(conn)["private_enum"]
    # Must not have advanced to/past done_b while pending_a is still incomplete.
    assert token is None or int(token["id"]) != completed_b.id

    client.iter_dialogs_calls.clear()
    refs2, skipped2, _ = asyncio.run(
        private_enum_mod.enumerate_private_dialogs(
            client, conn, max_dialogs=2, skip_complete=True
        )
    )
    assert "@pending_a" in refs2
    conn.close()


def test_backfill_quantum_advances_one_incomplete_dialog_at_a_time(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    archive_root = tmp_path / "archive"
    config = Config(
        default_account=None,
        accounts={},
        archive_root=archive_root,
    )
    directory = archive_store.ensure_account_dir(archive_root, "main")
    conn = archive_store.connect(archive_store.db_path_for(directory))
    archive_store.ensure_meta(conn, account_user_id=42, account_alias="main")
    conn.close()

    alice = _user(7, "alice")
    bob = _user(8, "bob")
    message = Message(
        id=1,
        peer_id=PeerUser(7),
        message="hello",
        date=datetime(2026, 8, 11, tzinfo=UTC),
        out=False,
    )
    client = FakeClient(
        me=User(
            id=42,
            is_self=True,
            access_hash=42,
            first_name="Me",
            username="me",
            phone=None,
        ),
        entities={"@alice": alice, "@bob": bob},
        messages=[message],
    )

    first = asyncio.run(
        archive_jobs_cmd.backfill_quantum(
            client,
            "main",
            chats=["@alice", "@bob"],
            private=False,
            limit=10,
            config=config,
        )
    )
    assert [item["chat"] for item in first["dialogs"]] == ["@alice"]
    assert first["remaining"] is True

    second = asyncio.run(
        archive_jobs_cmd.backfill_quantum(
            client,
            "main",
            chats=["@alice", "@bob"],
            private=False,
            limit=10,
            config=config,
        )
    )
    assert [item["chat"] for item in second["dialogs"]] == ["@bob"]
    assert second["remaining"] is False


def test_backfill_quantum_should_stop_prevents_media_tail(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    archive_root = tmp_path / "archive"
    config = Config(default_account=None, accounts={}, archive_root=archive_root)
    directory = archive_store.ensure_account_dir(archive_root, "main")
    conn = archive_store.connect(archive_store.db_path_for(directory))
    archive_store.ensure_meta(conn, account_user_id=42, account_alias="main")
    archive_store.upsert_message(
        conn,
        7,
        {
            "id": 1,
            "date": "2026-08-11T00:00:00+00:00",
            "text": "pending voice",
            "from": {"id": 7},
            "media": "MessageMediaDocument",
            "media_info": {"mime": "audio/ogg", "size": 10},
            "media_kind": "voice",
        },
    )
    conn.commit()
    conn.close()

    alice = _user(7, "alice")
    client = FakeClient(
        me=User(
            id=42,
            is_self=True,
            access_hash=42,
            first_name="Me",
            username="me",
            phone=None,
        ),
        entities={"@alice": alice},
    )
    downloads = []

    async def unexpected_download(_tg, source, *_args, **_kwargs):
        downloads.append(source.message_id)
        raise AssertionError("stopped job must not start media")

    monkeypatch.setattr(
        archive_jobs_cmd.sync_mod.media_cmd, "download_media", unexpected_download
    )
    result = asyncio.run(
        archive_jobs_cmd.backfill_quantum(
            client,
            "main",
            chats=["@alice"],
            private=False,
            limit=10,
            config=config,
            should_stop=lambda: True,
        )
    )

    assert result["dialogs"] == []
    assert result["media"]["downloaded"] == 0
    assert result["media"]["failed"] == []
    assert result["media"]["remaining"] is True
    assert result["remaining"] is True
    assert downloads == []

    conn = archive_store.connect(archive_store.db_path_for(directory))
    try:
        assert archive_store.transcript_row(conn, 7, 1)["media_attempts"] == 0
    finally:
        conn.close()


def test_progress_token_changes_when_a_dialog_cursor_advances(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    archive_root = tmp_path / "archive"
    config = Config(default_account=None, accounts={}, archive_root=archive_root)
    directory = archive_store.ensure_account_dir(archive_root, "main")
    conn = archive_store.connect(archive_store.db_path_for(directory))
    archive_store.ensure_meta(conn, account_user_id=42, account_alias="main")
    archive_store.upsert_sync_state(conn, 7, oldest_id=100, newest_id=120, more=True)
    conn.commit()
    before = archive_jobs_cmd.progress_token("main", config)

    archive_store.upsert_sync_state(conn, 7, oldest_id=50, newest_id=120, more=True)
    conn.commit()
    conn.close()
    after = archive_jobs_cmd.progress_token("main", config)

    assert after != before


def test_progress_token_changes_when_backfill_marks_a_dialog_complete(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    archive_root = tmp_path / "archive"
    config = Config(default_account=None, accounts={}, archive_root=archive_root)
    directory = archive_store.ensure_account_dir(archive_root, "main")
    conn = archive_store.connect(archive_store.db_path_for(directory))
    archive_store.ensure_meta(conn, account_user_id=42, account_alias="main")
    archive_store.upsert_sync_state(
        conn,
        7,
        oldest_id=1,
        newest_id=120,
        more=False,
        touch_sync=True,
    )
    conn.commit()
    before = archive_jobs_cmd.progress_token("main", config)

    archive_store.upsert_sync_state(
        conn,
        7,
        oldest_id=1,
        newest_id=120,
        more=False,
    )
    conn.commit()
    conn.close()
    after = archive_jobs_cmd.progress_token("main", config)

    assert after != before
