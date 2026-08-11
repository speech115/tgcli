"""Command-owned archive quantum behavior for ADR-0087."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from telethon.tl.types import Message, PeerUser, User

from tests.conftest import FakeClient
from tgcli.archive import store as archive_store
from tgcli.commands import archive_jobs as archive_jobs_cmd


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
    directory = archive_store.ensure_account_dir(
        archive_store.default_archive_root(), "main"
    )
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
        )
    )
    assert [item["chat"] for item in second["dialogs"]] == ["@bob"]
    assert second["remaining"] is False
