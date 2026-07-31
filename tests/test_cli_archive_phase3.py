"""`tg archive` Phase 3 boundary tests (ADR-0068): private resolve, --private, sync."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon import utils
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.updates import GetDifferenceRequest, GetStateRequest
from telethon.tl.types import (
    Channel,
    Message,
    PeerChannel,
    PeerUser,
    UpdateChannelTooLong,
    UpdateDeleteMessages,
    UpdateEditMessage,
    User,
)
from telethon.tl.types.updates import (
    Difference,
    DifferenceTooLong,
    State,
)

from tests.conftest import FakeClient, make_session_fake
from tgcli import changes_cursor
from tgcli.archive import (
    backfill as backfill_mod,
    store as store_mod,
    sync as sync_mod,
)
from tgcli.changes_cursor import ChangesCursor
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


def _user(*, user_id: int = 7, username: str = "alice", first: str = "Alice"):
    return User(
        id=user_id,
        is_self=False,
        access_hash=2,
        first_name=first,
        username=username,
        phone="200",
    )


def _msg(*, mid: int = 1, text: str = "hi", peer=None, edit_date=None):
    return Message(
        id=mid,
        peer_id=peer or PeerUser(7),
        message=text,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        out=False,
        edit_date=edit_date,
    )


def _client(*, me=None, entities=None, messages=(), dialogs=()):
    return FakeClient(
        me=me or _me(),
        entities=entities or {},
        messages=list(messages),
        dialogs=list(dialogs),
    )


def _state(pts=10, qts=1, seq=2, date=None):
    return State(
        pts=pts,
        qts=qts,
        date=date or datetime(2026, 1, 1, tzinfo=UTC),
        seq=seq,
        unread_count=0,
    )


def _init(monkeypatch, client=None):
    make_session_fake(monkeypatch, client or _client())
    assert main(["archive", "init", "--json"]) == 0


def _dialog(entity, *, name=None):
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


def test_search_resolves_private_username_from_sync_state(
    config_env, monkeypatch, capsys
):
    _init(monkeypatch)
    capsys.readouterr()
    path = archive_cmd.db_path("main")
    conn = store_mod.connect(path)
    try:
        store_mod.upsert_message(
            conn,
            7,
            {
                "id": 1,
                "date": "2026-01-02T00:00:00+00:00",
                "text": "private ёлка note",
                "from": {"id": 7},
            },
        )
        store_mod.upsert_sync_state(
            conn,
            7,
            oldest_id=1,
            newest_id=1,
            more=False,
            kind="user",
            title="Alice",
            username="alice",
            chat_ref="@alice",
        )
        conn.commit()
    finally:
        conn.close()

    from contextlib import asynccontextmanager

    from tgcli import session

    @asynccontextmanager
    async def boom(*_a, **_k):
        raise AssertionError("offline")
        yield  # pragma: no cover

    monkeypatch.setattr(session, "client", boom)
    assert main(["archive", "search", "елка", "--chat", "@alice", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["peer_id"] == 7
    assert len(data["hits"]) == 1
    assert data["hits"][0]["message_id"] == 1


def test_backfill_persists_identity_for_private_peer(config_env, monkeypatch, capsys):
    user = _user()
    client = _client(
        entities={"@alice": user, 7: user},
        messages=[_msg(mid=2, text="b"), _msg(mid=1, text="a")],
    )
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "backfill", "@alice", "--limit", "2", "--json"]) == 0
    capsys.readouterr()
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        state = store_mod.get_sync_state(conn, 7)
        assert state is not None
        assert state["kind"] == "user"
        assert state["username"] == "alice"
        assert state["chat_ref"] == "@alice"
        assert state["title"] == "Alice"
    finally:
        conn.close()


def test_backfill_private_requires_flag_and_caps(config_env, monkeypatch, capsys):
    assert main(["archive", "backfill", "--json"]) == 2
    err = capsys.readouterr().err.lower()
    assert "chat" in err or "private" in err

    make_session_fake(monkeypatch, _client())
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert (
        main(["archive", "backfill", "--private", "--max-dialogs", "0", "--json"]) == 2
    )
    assert "max-dialogs" in capsys.readouterr().err.lower()
    over = str(archive_cmd.MAX_PRIVATE_DIALOGS + 1)
    assert (
        main(["archive", "backfill", "--private", "--max-dialogs", over, "--json"]) == 2
    )
    assert "max-dialogs" in capsys.readouterr().err.lower()
    assert main(["archive", "backfill", "--private", "@alice", "--json"]) == 2
    assert "private" in capsys.readouterr().err.lower()


def test_backfill_private_enumerates_users_and_skips_complete(
    config_env, monkeypatch, capsys
):
    alice = _user(user_id=7, username="alice")
    bob = _user(user_id=8, username="bob", first="Bob")
    dialogs = [_dialog(alice, name="Alice"), _dialog(bob, name="Bob")]
    client = _client(
        entities={"@alice": alice, 7: alice, "@bob": bob, 8: bob},
        messages=[_msg(mid=1, text="hi", peer=PeerUser(7))],
        dialogs=dialogs,
    )
    # Map messages per entity for FakeClient: same list for all.
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    # Prefill bob as complete so --private skips it.
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        store_mod.upsert_sync_state(
            conn,
            8,
            oldest_id=1,
            newest_id=1,
            more=False,
            kind="user",
            title="Bob",
            username="bob",
            chat_ref="@bob",
        )
        conn.commit()
    finally:
        conn.close()
    assert (
        main(
            [
                "archive",
                "backfill",
                "--private",
                "--max-dialogs",
                "5",
                "--limit",
                "10",
                "--json",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "private"
    peers = {d["peer_id"] for d in data["dialogs"]}
    assert 7 in peers
    assert 8 not in peers
    assert data["skipped_complete"] >= 1


@pytest.mark.asyncio
async def test_private_backfill_does_not_skip_delta_only_state(tmp_path):
    alice = _user()
    client = _client(dialogs=[_dialog(alice, name="Alice")])
    conn = store_mod.connect(tmp_path / "archive.db")
    try:
        store_mod.upsert_sync_state(
            conn,
            7,
            oldest_id=42,
            newest_id=42,
            more=False,
            kind="user",
            title="Alice",
            username="alice",
            chat_ref="@alice",
            touch_sync=True,
        )
        refs, skipped = await backfill_mod.enumerate_private_dialogs(
            client,
            conn,
            max_dialogs=5,
        )
        assert refs == ["@alice"]
        assert skipped == 0
    finally:
        conn.close()


def test_sync_applies_edit_revision_and_private_delete_tombstone(
    config_env, monkeypatch, capsys
):
    user = _user()
    path_holder = {}

    class SyncClient(FakeClient):
        async def __call__(self, request):
            from telethon.tl import functions

            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=5, qts=1, seq=1)
            if isinstance(request, GetDifferenceRequest):
                return Difference(
                    new_messages=[],
                    new_encrypted_messages=[],
                    other_updates=[
                        UpdateEditMessage(
                            message=_msg(
                                mid=1,
                                text="edited",
                                peer=PeerUser(7),
                                edit_date=datetime(2026, 1, 3, tzinfo=UTC),
                            ),
                            pts=6,
                            pts_count=1,
                        ),
                        UpdateDeleteMessages(messages=[1], pts=7, pts_count=1),
                    ],
                    chats=[],
                    users=[user],
                    state=_state(pts=7, qts=1, seq=2),
                )
            if isinstance(request, functions.updates.GetStateRequest):
                return _state(pts=5, qts=1, seq=1)
            raise AssertionError(f"unexpected {request!r}")

    client = SyncClient(me=_me(), entities={"@alice": user, 7: user})
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    path = archive_cmd.db_path("main")
    path_holder["path"] = path
    conn = store_mod.connect(path)
    try:
        store_mod.upsert_message(
            conn,
            7,
            {
                "id": 1,
                "date": "2026-01-02T00:00:00+00:00",
                "text": "original",
                "from": {"id": 7},
            },
        )
        store_mod.upsert_sync_state(
            conn,
            7,
            oldest_id=1,
            newest_id=1,
            more=False,
            kind="user",
            title="Alice",
            username="alice",
            chat_ref="@alice",
        )
        # Seed an account cursor so sync does not only init.
        cursor = ChangesCursor(pts=5, qts=1, date=1_735_689_600, seq=1, channels={})
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
        conn.commit()
    finally:
        conn.close()

    assert main(["archive", "sync", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["applied"]["edits"] >= 1
    assert data["applied"]["tombstones"] >= 1
    conn = store_mod.connect(path)
    try:
        assert store_mod.counts(conn)["revisions"] >= 1
        assert store_mod.counts(conn)["tombstones"] >= 1
        assert store_mod.read_account_sync(conn)["changes_cursor"]
        assert store_mod.read_account_sync(conn)["gap"] is None
    finally:
        conn.close()


def test_sync_records_gap_and_rebaseline_clears_it(config_env, monkeypatch, capsys):
    class GapClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=50, qts=2, seq=9)
            if isinstance(request, GetDifferenceRequest):
                return DifferenceTooLong(pts=50)
            raise AssertionError(request)

    client = GapClient(me=_me())
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    cursor = ChangesCursor(pts=5, qts=1, date=1_735_689_600, seq=1, channels={})
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
        conn.commit()
    finally:
        conn.close()

    assert main(["archive", "sync", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["gap"] is not None
    assert data["gap"]["reason"] == "differenceTooLong"
    assert main(["archive", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["gap"] is not None

    assert main(["archive", "rebaseline", "--json"]) == 0
    rebased = json.loads(capsys.readouterr().out)
    assert rebased["rebaselined"] is True
    assert rebased["gap"] is None
    assert main(["archive", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["gap"] is None


def test_sync_and_rebaseline_readonly_blocked(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, _client())
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["--readonly", "archive", "sync", "--json"]) == 2
    assert "readonly" in capsys.readouterr().err.lower()
    assert main(["--readonly", "archive", "rebaseline", "--json"]) == 2
    assert "readonly" in capsys.readouterr().err.lower()


def test_sync_rejects_non_positive_event_cap(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, _client())
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "sync", "--max-events", "0", "--json"]) == 2
    assert "max-events" in capsys.readouterr().err.lower()


def test_sync_rejects_over_cap_events_and_dialogs(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, _client())
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    over_events = str(archive_cmd.MAX_SYNC_EVENTS + 1)
    assert main(["archive", "sync", "--max-events", over_events, "--json"]) == 2
    assert "max-events" in capsys.readouterr().err.lower()
    assert main(["archive", "sync", "--max-dialogs", "0", "--json"]) == 2
    assert "max-dialogs" in capsys.readouterr().err.lower()


def test_sync_rejects_invalid_media_budget(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, _client())
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "sync", "--max-media", "0", "--json"]) == 2
    assert "max-media" in capsys.readouterr().err.lower()
    over = str(archive_cmd.MAX_SYNC_MEDIA + 1)
    assert main(["archive", "sync", "--max-media", over, "--json"]) == 2
    assert "max-media" in capsys.readouterr().err.lower()
    over_dialogs = str(archive_cmd.MAX_SYNC_DIALOGS + 1)
    assert main(["archive", "sync", "--max-dialogs", over_dialogs, "--json"]) == 2
    assert "max-dialogs" in capsys.readouterr().err.lower()


def test_sync_and_rebaseline_verify_live_account_id(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, _client(me=_me(user_id=42)))
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    make_session_fake(monkeypatch, _client(me=_me(user_id=99)))
    assert main(["archive", "sync", "--json"]) == 2
    assert "account" in capsys.readouterr().err.lower()
    assert main(["archive", "rebaseline", "--json"]) == 2
    assert "account" in capsys.readouterr().err.lower()


def test_private_delete_tombstones_every_peer_sharing_message_id(tmp_path):
    """MTProto private deletes omit peer; local id match may hit many dialogs."""
    conn = store_mod.connect(tmp_path / "archive.db")
    try:
        store_mod.ensure_meta(conn, account_user_id=42, account_alias="main")
        for peer in (7, 8):
            store_mod.upsert_message(
                conn,
                peer,
                {
                    "id": 42,
                    "date": "2026-01-02T00:00:00+00:00",
                    "text": f"shared-id peer {peer}",
                    "from": {"id": peer},
                },
            )
        channel_peer = -1001234567890
        store_mod.upsert_message(
            conn,
            channel_peer,
            {
                "id": 42,
                "date": "2026-01-02T00:00:00+00:00",
                "text": "channel collision",
                "from": {"id": 1},
            },
        )
        conn.commit()
        applied = sync_mod.apply_events(
            conn,
            [{"type": "message_delete", "peer": None, "ids": [42]}],
        )
        assert applied["tombstones"] == 2
        peers = {
            int(row["peer_id"])
            for row in conn.execute("SELECT peer_id FROM tombstones").fetchall()
        }
        assert peers == {7, 8}
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM tombstones WHERE peer_id = ?",
                (channel_peer,),
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_apply_events_applies_full_batch_without_truncation(tmp_path):
    """Difference events are cheap locally — never drop a tail while syncing."""
    conn = store_mod.connect(tmp_path / "archive.db")
    try:
        store_mod.ensure_meta(conn, account_user_id=42, account_alias="main")
        events = [
            {
                "type": "message_new",
                "peer": 7,
                "message": {
                    "id": i,
                    "date": "2026-01-02T00:00:00+00:00",
                    "text": f"msg {i}",
                    "from": {"id": 7},
                },
            }
            for i in range(1, 601)
        ]
        applied = sync_mod.apply_events(conn, events)
        assert applied["events"] == 600
        assert applied["inserted"] == 600
        assert "truncated" not in applied
        assert (
            conn.execute("SELECT COUNT(*) FROM messages WHERE peer_id = 7").fetchone()[
                0
            ]
            == 600
        )
        state = store_mod.get_sync_state(conn, 7)
        assert state is not None
        assert state["more"] is True
        assert state["last_backfill_at"] is None
    finally:
        conn.close()


def test_sync_channel_activity_catchup_for_scoped_channel(
    config_env, monkeypatch, capsys
):
    channel = Channel(
        id=1234,
        title="News",
        photo=None,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        access_hash=99,
        megagroup=False,
        username="news",
    )
    peer = utils.get_peer_id(channel)
    catchup = Message(
        id=10,
        peer_id=PeerChannel(1234),
        message="caught up",
        date=datetime(2026, 1, 4, tzinfo=UTC),
        out=False,
    )

    class CatchupClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetFullChannelRequest):
                # Force skip subscription so UpdateChannelTooLong stays visible.
                raise RuntimeError("pts unavailable")
            if isinstance(request, GetStateRequest):
                return _state(pts=5, qts=1, seq=1)
            if isinstance(request, GetDifferenceRequest):
                return Difference(
                    new_messages=[],
                    new_encrypted_messages=[],
                    other_updates=[UpdateChannelTooLong(channel_id=1234, pts=None)],
                    chats=[channel],
                    users=[],
                    state=_state(pts=6, qts=1, seq=2),
                )
            raise AssertionError(request)

        async def get_entity(self, key):
            if key in (peer, 1234, channel, "@news", "news"):
                return channel
            return await super().get_entity(key)

    client = CatchupClient(
        me=_me(),
        entities={peer: channel, 1234: channel, "@news": channel, "news": channel},
        messages=[catchup],
        message_total=1,
    )
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    path = archive_cmd.db_path("main")
    conn = store_mod.connect(path)
    try:
        store_mod.add_scope(
            conn,
            peer_id=peer,
            kind="channel",
            title="News",
            username="news",
            chat_ref="@news",
        )
        store_mod.upsert_sync_state(
            conn,
            peer,
            oldest_id=1,
            newest_id=5,
            more=False,
            kind="channel",
            title="News",
            username="news",
            chat_ref="@news",
        )
        cursor = ChangesCursor(pts=5, qts=1, date=1_735_689_600, seq=1, channels={})
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
        conn.commit()
    finally:
        conn.close()

    assert main(["archive", "sync", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["applied"]["channel_activity"] >= 1
    assert data["catchups"]
    assert data["catchups"][0]["peer_id"] == peer
    assert data["catchups"][0]["stored"] >= 1
    assert client.iter_messages_calls
    assert client.iter_messages_calls[0][2] == sync_mod.CATCHUP_LIMIT
    assert client.iter_messages_kwargs.get("min_id") == 5
    conn = store_mod.connect(path)
    try:
        row = conn.execute(
            "SELECT text FROM messages WHERE peer_id = ? AND message_id = ?",
            (peer, 10),
        ).fetchone()
        assert row is not None
        assert row["text"] == "caught up"
    finally:
        conn.close()


def test_sync_new_private_dialog_enters_sync_state(config_env, monkeypatch, capsys):
    user = _user(user_id=9, username="carol", first="Carol")

    class NewPeerClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=5, qts=1, seq=1)
            if isinstance(request, GetDifferenceRequest):
                return Difference(
                    new_messages=[_msg(mid=3, text="hello carol", peer=PeerUser(9))],
                    new_encrypted_messages=[],
                    other_updates=[],
                    chats=[],
                    users=[user],
                    state=_state(pts=6, qts=1, seq=2),
                )
            raise AssertionError(request)

        async def get_entity(self, key):
            if key in (9, "carol", "@carol") or key == user:
                return user
            return await super().get_entity(key)

    client = NewPeerClient(me=_me(), entities={9: user, "@carol": user, "carol": user})
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    cursor = ChangesCursor(pts=5, qts=1, date=1_735_689_600, seq=1, channels={})
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
        conn.commit()
    finally:
        conn.close()
    assert main(["archive", "sync", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["applied"]["inserted"] >= 1
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        state = store_mod.get_sync_state(conn, 9)
        assert state is not None
        assert state["kind"] == "user"
        assert state["username"] == "carol" or state["title"] == "Carol"
    finally:
        conn.close()
