"""Archive fakes shared by the archive tests: users, a fake client, a seeded store."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from telethon.tl.types import Message, PeerUser, User

from tests.conftest import FakeClient, make_session_fake
from tgcli.cli import main
from tgcli.commands import archive as archive_cmd


def archive_me(*, user_id: int = 42):
    return User(
        id=user_id,
        is_self=True,
        access_hash=1,
        first_name="Me",
        username="me",
        phone="100",
    )


def archive_user(*, user_id: int = 7, username: str = "alice", first: str = "Alice"):
    return User(
        id=user_id,
        is_self=False,
        access_hash=2,
        first_name=first,
        username=username,
        phone="200",
    )


def archive_client(*, me=None, entities=None, messages=(), dialogs=()):
    return FakeClient(
        me=me or archive_me(),
        entities=entities or {},
        messages=list(messages),
        dialogs=list(dialogs),
    )


def archive_msg(*, mid: int = 1, text: str = "hi", peer=None, edit_date=None):
    return Message(
        id=mid,
        peer_id=peer or PeerUser(7),
        message=text,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        out=False,
        edit_date=edit_date,
    )


def archive_dialog(entity, *, name=None):
    return SimpleNamespace(
        id=entity.id,
        name=name or getattr(entity, "first_name", None) or "X",
        entity=entity,
        is_user=isinstance(entity, User),
        is_group=False,
        is_channel=False,
        unread_count=0,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        dialog=SimpleNamespace(unread_mentions_count=0),
    )


def init_archive(monkeypatch, client=None):
    make_session_fake(monkeypatch, client or archive_client())
    assert main(["archive", "init", "--json"]) == 0


def seed_search_corpus(monkeypatch):
    """Init store + insert searchable rows without a live Telegram read path."""
    from tgcli.archive import store as store_mod

    make_session_fake(monkeypatch, archive_client())
    assert main(["archive", "init", "--json"]) == 0
    path = archive_cmd.db_path("main")
    conn = store_mod.connect(path)
    try:
        store_mod.ensure_meta(conn, account_user_id=42, account_alias="main")
        store_mod.add_scope(
            conn,
            peer_id=-1001234,
            kind="channel",
            title="News",
            username="news",
            chat_ref="@news",
        )
        store_mod.upsert_message(
            conn,
            7,
            {
                "id": 1,
                "date": "2026-01-02T00:00:00+00:00",
                "text": "сообщение про ёлка",
                "from": {"id": 7},
            },
        )
        store_mod.upsert_message(
            conn,
            7,
            {
                "id": 2,
                "date": "2026-01-03T00:00:00+00:00",
                "text": "хакатоны S26 рядом",
                "from": {"id": 7},
            },
        )
        store_mod.upsert_message(
            conn,
            -1001234,
            {
                "id": 10,
                "date": "2026-01-04T00:00:00+00:00",
                "text": "channel хакатон note",
                "from": {"id": 1},
            },
        )
        store_mod.upsert_sync_state(
            conn, 7, oldest_id=1, newest_id=2, more=True, last_error=None
        )
        store_mod.upsert_sync_state(
            conn, -1001234, oldest_id=10, newest_id=10, more=False, last_error=None
        )
        conn.commit()
    finally:
        conn.close()


def block_telegram_session(monkeypatch):
    from contextlib import asynccontextmanager

    from tgcli import session

    opened = {"n": 0}

    @asynccontextmanager
    async def boom(*_a, **_k):
        opened["n"] += 1
        raise AssertionError("offline archive must not open a Telegram session")
        yield  # pragma: no cover

    monkeypatch.setattr(session, "client", boom)
    return opened
