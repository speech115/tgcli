"""Command-owned archive quantum behavior for ADR-0087."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from telethon.tl.types import Message, PeerUser, User

from tests.conftest import FakeClient
from tgcli.archive import store as archive_store
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
