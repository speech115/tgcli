"""`tg archive` Phase 3 boundary tests (ADR-0068): private resolve, --private, sync."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors, utils
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.updates import (
    GetChannelDifferenceRequest,
    GetDifferenceRequest,
    GetStateRequest,
)
from telethon.tl.types import (
    Channel,
    Chat,
    Message,
    PeerChannel,
    PeerUser,
    UpdateChannelTooLong,
    UpdateDeleteChannelMessages,
    UpdateDeleteMessages,
    UpdateEditMessage,
    User,
)
from telethon.tl.types.updates import (
    ChannelDifference,
    ChannelDifferenceEmpty,
    ChannelDifferenceTooLong,
    Difference,
    DifferenceEmpty,
    DifferenceTooLong,
    State,
)

from tests.conftest import FakeClient, make_session_fake
from tgcli.archive import (
    backfill as backfill_mod,
    changes_cursor,
    limits,
    store as store_mod,
    sync as sync_mod,
)
from tgcli.archive.changes_cursor import ChangesCursor
from tgcli.cli import main
from tgcli.commands import archive as archive_cmd
from tgcli.governor import pacing

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


def test_backfill_max_runtime_does_not_start_media_tail(
    config_env, monkeypatch, capsys
):
    user = _user()
    client = _client(
        entities={"@alice": user, 7: user},
        messages=[_msg(mid=1, text="hi")],
    )
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()

    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        store_mod.upsert_message(
            conn,
            7,
            {
                "id": 99,
                "date": "2026-01-02T00:00:00+00:00",
                "text": "pending voice",
                "from": {"id": 7},
                "media": "MessageMediaDocument",
                "media_info": {"mime": "audio/ogg", "size": 10},
                "media_kind": "voice",
            },
        )
        conn.commit()
    finally:
        conn.close()

    downloads = []

    async def unexpected_download(_tg, source, *_args, **_kwargs):
        downloads.append(source.message_id)
        raise AssertionError("expired backfill must not start media")

    monkeypatch.setattr(sync_mod.media_cmd, "download_media", unexpected_download)
    monkeypatch.setattr(pacing, "wall_clock_remaining", lambda: 0.0)

    assert (
        main(
            [
                "archive",
                "backfill",
                "@alice",
                "--max-runtime",
                "0.001",
                "--json",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["stop_reason"] == "wall_clock_cap"
    assert data["media"]["downloaded"] == 0
    assert data["media"]["failed"] == []
    assert data["media"]["remaining"] is True
    assert downloads == []

    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        assert store_mod.transcript_row(conn, 7, 99)["media_attempts"] == 0
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
    over = str(limits.BOUNDS["backfill", "--max-dialogs"].maximum + 1)
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


def test_backfill_private_resumes_unfinished_dialog_before_a_finished_one(
    config_env, monkeypatch, capsys
):
    class PerPeerClient(FakeClient):
        async def iter_messages(self, entity, limit=None, offset_id=0, **_kwargs):
            page = [
                message
                for message in self._messages
                if message.peer_id.user_id == entity.id
                and (not offset_id or message.id < offset_id)
            ]
            for message in page[:limit]:
                yield message

    alice = _user(user_id=7, username="alice")
    bob = _user(user_id=8, username="bob", first="Bob")
    client = PerPeerClient(
        me=_me(),
        entities={"@alice": alice, 7: alice, "@bob": bob, 8: bob},
        messages=[
            _msg(mid=3, peer=PeerUser(7)),
            _msg(mid=2, peer=PeerUser(7)),
            _msg(mid=1, peer=PeerUser(7)),
            _msg(mid=5, peer=PeerUser(8)),
        ],
        dialogs=[_dialog(alice, name="Alice"), _dialog(bob, name="Bob")],
    )
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    backfill = ["archive", "backfill", "--private", "--limit", "2", "--json"]

    assert main(backfill) == 0
    first = json.loads(capsys.readouterr().out)
    assert [(d["peer_id"], d["more"]) for d in first["dialogs"]] == [
        (7, True),
        (8, False),
    ]

    assert main(backfill) == 0
    second = json.loads(capsys.readouterr().out)
    assert [d["peer_id"] for d in second["dialogs"]] == [7]


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
        refs, skipped, _cursors = await backfill_mod.enumerate_private_dialogs(
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
    assert data["remaining"] is False
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


@pytest.mark.parametrize(
    "refusal",
    [
        telethon_errors.ChannelPrivateError,
        telethon_errors.ChannelInvalidError,
        telethon_errors.ChannelBannedError,
    ],
)
def test_sync_survives_a_subscribed_channel_the_account_lost(
    config_env, monkeypatch, capsys, refusal
):
    """One channel that turned private, was deleted, or banned the account
    used to abort every `archive sync` before the cursor was saved, so the
    private chats and every other channel stopped advancing for good."""

    def broadcast(channel_id):
        return Channel(
            id=channel_id,
            title=f"C{channel_id}",
            photo=None,
            date=datetime(2026, 1, 1, tzinfo=UTC),
            access_hash=99,
            megagroup=False,
        )

    lost, live = broadcast(1234), broadcast(5678)
    lost_peer, live_peer = utils.get_peer_id(lost), utils.get_peer_id(live)

    class LostChannelClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=5, qts=1, seq=1)
            if isinstance(request, GetDifferenceRequest):
                return Difference(
                    new_messages=[],
                    new_encrypted_messages=[],
                    other_updates=[],
                    chats=[],
                    users=[],
                    state=_state(pts=9, qts=1, seq=2),
                )
            if isinstance(request, GetChannelDifferenceRequest):
                if request.channel.channel_id == lost.id:
                    raise refusal(request=request)
                return ChannelDifferenceEmpty(pts=20, final=True)
            if isinstance(request, GetFullChannelRequest):
                raise refusal(request=request)
            raise AssertionError(request)

    client = LostChannelClient(me=_me(), entities={lost_peer: lost, live_peer: live})
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    path = archive_cmd.db_path("main")
    conn = store_mod.connect(path)
    try:
        for channel, peer in ((lost, lost_peer), (live, live_peer)):
            store_mod.add_scope(
                conn,
                peer_id=peer,
                kind="channel",
                title=channel.title,
                username=None,
                chat_ref=str(peer),
            )
        cursor = ChangesCursor(
            pts=5,
            qts=1,
            date=1_735_689_600,
            seq=1,
            channels={lost_peer: 3, live_peer: 4},
        )
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
        conn.commit()
    finally:
        conn.close()

    assert main(["archive", "sync", "--json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["gap"]["scope"] == lost_peer
    assert data["gap"]["reason"] == "channelUnavailable"
    assert f"{lost_peer}" in captured.err
    conn = store_mod.connect(path)
    try:
        saved = changes_cursor.decode(
            store_mod.read_account_sync(conn)["changes_cursor"]
        )
    finally:
        conn.close()
    assert saved.pts == 9
    assert saved.channels == {live_peer: 20}

    # The next run neither aborts nor reports the same loss again.
    assert main(["archive", "sync", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gap"] is None


def _broadcast(channel_id: int) -> Channel:
    return Channel(
        id=channel_id,
        title=f"C{channel_id}",
        photo=None,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        access_hash=99,
        megagroup=False,
    )


def _seed_channel_cursor(channels: list[tuple[Channel, int]]) -> None:
    """Scope each channel and store a cursor subscribed at the given pts."""
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        for channel, _pts in channels:
            store_mod.add_scope(
                conn,
                peer_id=utils.get_peer_id(channel),
                kind="channel",
                title=channel.title,
                username=None,
                chat_ref=str(utils.get_peer_id(channel)),
            )
        cursor = ChangesCursor(
            pts=5,
            qts=1,
            date=1_735_689_600,
            seq=1,
            channels={utils.get_peer_id(ch): pts for ch, pts in channels},
        )
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
        conn.commit()
    finally:
        conn.close()


def _saved_cursor() -> ChangesCursor:
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        return changes_cursor.decode(
            store_mod.read_account_sync(conn)["changes_cursor"]
        )
    finally:
        conn.close()


def test_sync_keeps_the_pages_read_before_a_channel_was_lost(
    config_env, monkeypatch, capsys
):
    """Access lost on a later page of a paginated channel difference used to
    throw away the pages already read; the peer left the cursor, so those
    messages were never stored and no later sync could fetch them again."""
    lost = _broadcast(1234)
    lost_peer = utils.get_peer_id(lost)

    class MidPollLossClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=5, qts=1, seq=1)
            if isinstance(request, GetDifferenceRequest):
                return DifferenceEmpty(date=datetime(2026, 1, 5, tzinfo=UTC), seq=1)
            if isinstance(request, GetChannelDifferenceRequest) and request.pts == 3:
                return ChannelDifference(
                    pts=6,
                    new_messages=[
                        _msg(mid=10, text="before loss", peer=PeerChannel(1234))
                    ],
                    other_updates=[],
                    chats=[lost],
                    users=[],
                    final=False,
                )
            # The next page, and any catch-up, finds the channel gone.
            raise telethon_errors.ChannelPrivateError(request=request)

    client = MidPollLossClient(me=_me(), entities={lost_peer: lost})
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    _seed_channel_cursor([(lost, 3)])

    assert main(["archive", "sync", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["gap"]["scope"] == lost_peer
    assert data["gap"]["reason"] == "channelUnavailable"
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        row = conn.execute(
            "SELECT text FROM messages WHERE peer_id = ? AND message_id = ?",
            (lost_peer, 10),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["text"] == "before loss"
    assert _saved_cursor().channels == {}


@pytest.mark.parametrize("second_failure", ["lost", "too_long"])
def test_sync_reports_every_channel_gap_one_pass_at_a_time(
    config_env, monkeypatch, capsys, second_failure
):
    """A pass reports one gap. A second channel losing updates in the same
    pass used to be dropped (or jumped ahead) anyway, so its loss was never
    reported. It now stays at its last applied pts until a later pass can
    report it."""
    first, second, live = _broadcast(1111), _broadcast(2222), _broadcast(3333)
    first_peer, second_peer, live_peer = (
        utils.get_peer_id(first),
        utils.get_peer_id(second),
        utils.get_peer_id(live),
    )
    reason = {"lost": "channelUnavailable", "too_long": "channelDifferenceTooLong"}[
        second_failure
    ]

    class TwoGapsClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=5, qts=1, seq=1)
            if isinstance(request, GetDifferenceRequest):
                return DifferenceEmpty(date=datetime(2026, 1, 5, tzinfo=UTC), seq=1)
            if isinstance(request, GetChannelDifferenceRequest):
                channel_id = request.channel.channel_id
                if channel_id == second.id:
                    raise telethon_errors.ChannelPrivateError(request=request)
                if channel_id == first.id:
                    if second_failure == "lost":
                        raise telethon_errors.ChannelPrivateError(request=request)
                    return ChannelDifferenceTooLong(
                        dialog=SimpleNamespace(pts=90),
                        messages=[],
                        chats=[],
                        users=[],
                        final=True,
                    )
                return ChannelDifferenceEmpty(pts=20, final=True)
            if isinstance(request, GetFullChannelRequest):
                raise telethon_errors.ChannelPrivateError(request=request)
            raise AssertionError(request)

    client = TwoGapsClient(
        me=_me(),
        entities={first_peer: first, second_peer: second, live_peer: live},
    )
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    # Peers are polled in sorted order: -1002222… comes before -1001111….
    assert sorted([first_peer, second_peer]) == [second_peer, first_peer]
    _seed_channel_cursor([(first, 3), (second, 4), (live, 7)])

    assert main(["archive", "sync", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["gap"]["scope"] == second_peer
    assert data["gap"]["reason"] == "channelUnavailable"
    saved = _saved_cursor()
    assert second_peer not in saved.channels
    assert saved.channels[first_peer] == 3
    assert saved.channels[live_peer] == 20

    assert main(["archive", "sync", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["gap"]["scope"] == first_peer
    assert data["gap"]["reason"] == reason


def test_rebaseline_and_sync_leave_a_scoped_basic_group_to_the_common_tier(
    config_env, monkeypatch, capsys
):
    """A basic group is a `group` in scope, but only channels have their own
    update stream. Rebaseline, the recovery every cursor error points to,
    failed with "not a channel/supergroup" for any account that scoped one,
    and every sync retried the subscription and failed again."""
    group = Chat(
        id=55,
        title="Family",
        photo=None,
        participants_count=3,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        version=1,
    )
    channel = Channel(
        id=1234,
        title="News",
        photo=None,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        access_hash=99,
        megagroup=False,
        username="news",
    )
    group_peer, channel_peer = utils.get_peer_id(group), utils.get_peer_id(channel)

    class ScopeClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=50, qts=2, seq=9)
            if isinstance(request, GetFullChannelRequest):
                assert request.channel is channel
                return SimpleNamespace(full_chat=SimpleNamespace(pts=7))
            if isinstance(request, GetDifferenceRequest):
                return DifferenceEmpty(date=datetime(2026, 1, 5, tzinfo=UTC), seq=9)
            if isinstance(request, GetChannelDifferenceRequest):
                return ChannelDifferenceEmpty(pts=7, final=True)
            raise AssertionError(request)

    client = ScopeClient(
        me=_me(),
        entities={
            group_peer: group,
            str(group_peer): group,
            channel_peer: channel,
            "@news": channel,
        },
    )
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        store_mod.add_scope(
            conn,
            peer_id=group_peer,
            kind="group",
            title="Family",
            username=None,
            chat_ref=str(group_peer),
        )
        store_mod.add_scope(
            conn,
            peer_id=channel_peer,
            kind="channel",
            title="News",
            username="news",
            chat_ref="@news",
        )
        conn.commit()
    finally:
        conn.close()

    assert main(["archive", "rebaseline", "--json"]) == 0
    rebased = json.loads(capsys.readouterr().out)
    assert rebased["peers"] == ["@news"]
    assert changes_cursor.decode(rebased["next_cursor"]).channels == {channel_peer: 7}

    assert main(["archive", "sync", "--json"]) == 0
    assert "skip channel subscribe" not in capsys.readouterr().err


def test_a_recorded_gap_survives_later_clean_syncs_until_rebaseline(
    config_env, monkeypatch, capsys
):
    """CONTRACT: the gap is stored loudly and rebaseline is what clears it.
    The next clean sync used to erase it, so a scheduled run that missed the
    one gapped JSON never learned edits and deletes were lost."""

    class OnceTooLongClient(FakeClient):
        too_long = True

        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=50, qts=2, seq=9)
            if isinstance(request, GetDifferenceRequest):
                if self.too_long:
                    self.too_long = False
                    return DifferenceTooLong(pts=50)
                return DifferenceEmpty(date=datetime(2026, 1, 5, tzinfo=UTC), seq=9)
            raise AssertionError(request)

    client = OnceTooLongClient(me=_me())
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
    assert json.loads(capsys.readouterr().out)["gap"]["reason"] == "differenceTooLong"
    assert main(["archive", "sync", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gap"]["reason"] == "differenceTooLong"

    assert main(["archive", "rebaseline", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gap"] is None


def test_every_uncleared_gap_stays_visible_until_rebaseline(
    config_env, monkeypatch, capsys
):
    """The archive stored one gap: a second pass that hit a different gap
    overwrote the first before `rebaseline`, so `archive status` silently
    forgot the earlier loss."""
    lost = _broadcast(1111)
    lost_peer = utils.get_peer_id(lost)

    class TooLongThenLostClient(FakeClient):
        too_long = True

        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=50, qts=2, seq=9)
            if isinstance(request, GetDifferenceRequest):
                if self.too_long:
                    self.too_long = False
                    return DifferenceTooLong(pts=50)
                return DifferenceEmpty(date=datetime(2026, 1, 5, tzinfo=UTC), seq=9)
            if isinstance(request, GetChannelDifferenceRequest | GetFullChannelRequest):
                if self.lost:
                    raise telethon_errors.ChannelPrivateError(request=request)
                return SimpleNamespace(full_chat=SimpleNamespace(pts=7))
            raise AssertionError(request)

    client = TooLongThenLostClient(me=_me(), entities={lost_peer: lost})
    client.lost = True
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    _seed_channel_cursor([(lost, 3)])

    assert main(["archive", "sync", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gap"]["scope"] == "common"
    assert main(["archive", "sync", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gap"]["scope"] == lost_peer
    assert main(["archive", "sync", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gap"] is None

    assert main(["archive", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert [(g["scope"], g["reason"]) for g in status["gaps"]] == [
        ("common", "differenceTooLong"),
        (lost_peer, "channelUnavailable"),
    ]
    assert status["gap"] == status["gaps"][-1]
    assert main(["--plain", "archive", "status"]) == 0
    plain = capsys.readouterr().out
    assert "gap\tchannelUnavailable" in plain
    assert "gaps\t2" in plain

    client.lost = False  # access is back, so rebaseline can resubscribe
    assert main(["archive", "rebaseline", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gaps"] == []
    assert main(["archive", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["gap"] is None
    assert status["gaps"] == []


@pytest.mark.parametrize(
    "stored", ["{not json", '"a string"', "[1, 2]", '[{"reason":"x"}, 3]']
)
def test_an_unreadable_v9_gap_migrates_to_a_gap_that_rebaseline_clears(
    config_env, monkeypatch, capsys, stored
):
    """The v10 migration parsed the stored gap unguarded: a malformed value
    crashed every archive command, rebaseline included, so the store could
    never be opened again. An unreadable gap must still read as a loss."""

    class StateClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=50, qts=2, seq=9)
            raise AssertionError(request)

    make_session_fake(monkeypatch, StateClient(me=_me()))
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        store_mod.write_account_sync(conn, changes_cursor="v1:stale")
        conn.execute("UPDATE account_sync SET gap_json = ? WHERE id = 1", (stored,))
        conn.execute("PRAGMA user_version=9")
        conn.commit()
    finally:
        conn.close()

    assert main(["archive", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["schema_version"] == store_mod.SCHEMA_VERSION
    assert [g["reason"] for g in status["gaps"]] == ["unreadableGap"]
    assert status["gap"]["scope"] == "common"
    assert status["gap"]["recover"]["edits_deletes"] == "lost"

    assert main(["archive", "rebaseline", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gaps"] == []


def test_sync_and_rebaseline_readonly_blocked(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, _client())
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["--readonly", "archive", "sync", "--json"]) == 2
    assert "readonly" in capsys.readouterr().err.lower()
    assert main(["--readonly", "archive", "rebaseline", "--json"]) == 2
    assert "readonly" in capsys.readouterr().err.lower()


def test_sync_and_rebaseline_refuse_while_another_run_holds_the_account(
    config_env, monkeypatch, capsys
):
    import fcntl

    class QuietClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=50, qts=2, seq=9)
            if isinstance(request, GetDifferenceRequest):
                return DifferenceEmpty(date=datetime(2026, 1, 5, tzinfo=UTC), seq=1)
            raise AssertionError(request)

    client = QuietClient(me=_me())
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    stored = changes_cursor.encode(
        ChangesCursor(pts=5, qts=1, date=1_735_689_600, seq=1, channels={})
    )
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        store_mod.write_account_sync(
            conn, changes_cursor=stored, pending_catchups=[-1001234567890]
        )
    finally:
        conn.close()

    def account_sync():
        conn = store_mod.connect(archive_cmd.db_path("main"))
        try:
            return store_mod.read_account_sync(conn)
        finally:
            conn.close()

    before = account_sync()
    lock = archive_cmd.account_dir("main") / "sync.lock"
    with lock.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for command in ("sync", "rebaseline"):
            assert main(["archive", command, "--json"]) == 3
            error = json.loads(capsys.readouterr().out)["error"]
            assert "already running" in error["message"]
    assert account_sync() == before
    assert not any(
        isinstance(request, (GetStateRequest, GetDifferenceRequest))
        for request in client.call_requests
    )
    assert main(["archive", "rebaseline", "--json"]) == 0
    assert account_sync()["changes_cursor"] != stored


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
    over_events = str(limits.BOUNDS["sync", "--max-events"].maximum + 1)
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
    over = str(limits.BOUNDS["sync", "--max-media"].maximum + 1)
    assert main(["archive", "sync", "--max-media", over, "--json"]) == 2
    assert "max-media" in capsys.readouterr().err.lower()
    over_dialogs = str(limits.BOUNDS["sync", "--max-dialogs"].maximum + 1)
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


def test_sync_channel_delete_outside_archive_scope_does_not_tombstone(
    config_env, monkeypatch, capsys
):
    channel = Channel(
        id=1234,
        title="Removed channel",
        photo=None,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        access_hash=99,
        megagroup=False,
        username="removed",
    )
    peer = utils.get_peer_id(channel)

    class DeleteClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetDifferenceRequest):
                return DifferenceEmpty(date=datetime(2026, 1, 5, tzinfo=UTC), seq=1)
            if isinstance(request, GetChannelDifferenceRequest):
                return ChannelDifference(
                    pts=4,
                    new_messages=[],
                    other_updates=[
                        UpdateDeleteChannelMessages(
                            channel_id=1234,
                            messages=[42],
                            pts=4,
                            pts_count=1,
                        )
                    ],
                    chats=[channel],
                    users=[],
                    final=True,
                )
            raise AssertionError(request)

    client = DeleteClient(
        me=_me(), entities={peer: channel, 1234: channel, "@removed": channel}
    )
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    path = archive_cmd.db_path("main")
    conn = store_mod.connect(path)
    try:
        store_mod.upsert_message(
            conn,
            peer,
            {
                "id": 42,
                "date": "2026-01-02T00:00:00+00:00",
                "text": "kept after archive remove",
                "from": {"id": 1},
            },
        )
        store_mod.upsert_sync_state(
            conn,
            peer,
            oldest_id=42,
            newest_id=42,
            more=False,
            kind="channel",
            title="Removed channel",
            chat_ref=str(peer),
        )
        cursor = ChangesCursor(
            pts=5, qts=1, date=1_735_689_600, seq=1, channels={peer: 3}
        )
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
        conn.commit()
    finally:
        conn.close()

    assert main(["archive", "sync", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["applied"]["events"] == 1
    assert data["applied"]["tombstones"] == 0
    assert data["applied"]["skipped_out_of_scope"] == 1

    conn = store_mod.connect(path)
    try:
        assert store_mod.counts(conn)["tombstones"] == 0
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


def test_remove_drops_channel_from_changes_cursor(config_env, monkeypatch, capsys):
    """T09: remove must unsubscribe the channel from the persisted cursor.

    Otherwise a later `archive sync` keeps polling `GetChannelDifference`
    for a channel that already left explicit scope.
    """
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

    class NoChannelPollClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=5, qts=1, seq=1)
            if isinstance(request, GetDifferenceRequest):
                return DifferenceEmpty(date=datetime(2026, 1, 5, tzinfo=UTC), seq=1)
            if isinstance(request, GetChannelDifferenceRequest):
                raise AssertionError(
                    "archive sync must not poll a channel dropped by remove"
                )
            raise AssertionError(request)

    client = NoChannelPollClient(
        me=_me(), entities={peer: channel, 1234: channel, "@news": channel}
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
        # Simulate an earlier sync that already subscribed this channel.
        cursor = ChangesCursor(
            pts=5, qts=1, date=1_735_689_600, seq=1, channels={peer: 3}
        )
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
        conn.commit()
    finally:
        conn.close()

    assert main(["archive", "remove", "@news", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["removed"]["kind"] == "channel"

    conn = store_mod.connect(path)
    try:
        encoded = store_mod.read_account_sync(conn)["changes_cursor"]
        assert encoded is not None
        decoded = changes_cursor.decode(encoded)
        assert peer not in decoded.channels
    finally:
        conn.close()

    assert main(["archive", "sync", "--json"]) == 0
    assert not any(
        isinstance(request, GetChannelDifferenceRequest)
        for request in client.call_requests
    )


def test_sync_max_runtime_stops_before_catchup_and_media(
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
    client = _client(
        entities={peer: channel},
        messages=[
            Message(
                id=10,
                peer_id=PeerChannel(1234),
                message="must stay deferred",
                date=datetime(2026, 1, 4, tzinfo=UTC),
                out=False,
            )
        ],
    )
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()

    cursor = ChangesCursor(pts=5, qts=1, date=1_735_689_600, seq=1, channels={})
    conn = store_mod.connect(archive_cmd.db_path("main"))
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
        store_mod.upsert_message(
            conn,
            peer,
            {
                "id": 9,
                "date": "2026-01-03T00:00:00+00:00",
                "text": "pending voice",
                "from": {"id": 7},
                "media": "MessageMediaDocument",
                "media_info": {"mime": "audio/ogg", "size": 10},
                "media_kind": "voice",
            },
        )
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
        conn.commit()
    finally:
        conn.close()

    async def keep_subscriptions(_tg, _conn, current):
        return current

    async def one_activity(_tg, current, **_kwargs):
        return (
            {
                "events": [{"type": "channel_activity", "peer": peer}],
                "gap": None,
                "skipped": {},
            },
            current,
            [],
        )

    downloads = []

    async def unexpected_download(_tg, source, *_args, **_kwargs):
        downloads.append(source.message_id)
        raise AssertionError("expired sync must not start media")

    monkeypatch.setattr(sync_mod, "_ensure_channel_subscriptions", keep_subscriptions)
    monkeypatch.setattr(sync_mod.changes_engine, "once", one_activity)
    monkeypatch.setattr(sync_mod.media_cmd, "download_media", unexpected_download)
    monkeypatch.setattr(pacing, "wall_clock_remaining", lambda: 0.0)

    assert main(["archive", "sync", "--max-runtime", "0.001", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["stop_reason"] == "wall_clock_cap"
    assert data["catchups"] == []
    assert data["media"]["downloaded"] == 0
    assert data["media"]["failed"] == []
    assert data["media"]["remaining"] is True
    assert data["remaining"] is True
    assert client.iter_messages_calls == []
    assert downloads == []


def test_remove_keeps_scope_when_changes_cursor_is_corrupt(
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
    client = _client(entities={peer: channel, 1234: channel, "@news": channel})
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "add", "@news", "--json"]) == 0
    capsys.readouterr()
    path = archive_cmd.db_path("main")
    conn = store_mod.connect(path)
    try:
        store_mod.write_account_sync(conn, changes_cursor="v1:not-valid")
    finally:
        conn.close()

    assert main(["archive", "remove", "@news", "--json"]) == 2
    assert "cursor is corrupt" in capsys.readouterr().err

    conn = store_mod.connect(path)
    try:
        assert store_mod.in_explicit_scope(conn, peer)
        assert store_mod.read_account_sync(conn)["changes_cursor"] == "v1:not-valid"
    finally:
        conn.close()


def test_inflight_sync_cannot_restore_removed_channel_subscription(
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

    class ConcurrentRemoveClient(FakeClient):
        removed = False

        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetDifferenceRequest):
                assert not self.removed
                await archive_cmd.remove_chat(self, "main", "@news")
                self.removed = True
                return DifferenceEmpty(date=datetime(2026, 1, 5, tzinfo=UTC), seq=1)
            if isinstance(request, GetChannelDifferenceRequest):
                return ChannelDifferenceEmpty(pts=4, final=True)
            raise AssertionError(request)

    client = ConcurrentRemoveClient(
        me=_me(), entities={peer: channel, 1234: channel, "@news": channel}
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
        cursor = ChangesCursor(
            pts=5, qts=1, date=1_735_689_600, seq=1, channels={peer: 3}
        )
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
    finally:
        conn.close()

    assert main(["archive", "sync", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert client.removed is True
    assert peer not in changes_cursor.decode(data["next_cursor"]).channels

    conn = store_mod.connect(path)
    try:
        assert not store_mod.in_explicit_scope(conn, peer)
        encoded = store_mod.read_account_sync(conn)["changes_cursor"]
        assert encoded is not None
        assert peer not in changes_cursor.decode(encoded).channels
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


def _flood_wait_error():
    flood_wait = telethon_errors.FloodWaitError(request=None)
    flood_wait.seconds = 30
    return flood_wait


def _governor_cooling():
    # What the governed client raises for a cooling request type, before any RPC.
    from tgcli.errors import RateLimitError

    return RateLimitError("GetFileRequest is rate limited for 600s", retry_after=600)


def _connection_lost():
    return ConnectionError("Connection to Telegram failed 5 time(s)")


def _auth_key_unregistered():
    return telethon_errors.AuthKeyUnregisteredError(request=None)


def _server_error():
    return telethon_errors.ServerError(request=None, message="INTERNAL", code=500)


@pytest.mark.parametrize(
    ("make_error", "exit_code", "code"),
    [
        (_flood_wait_error, 5, "FLOOD_WAIT"),
        (_governor_cooling, 5, "FLOOD_WAIT"),
        (_connection_lost, 1, "RUNTIME"),
        (_auth_key_unregistered, 1, "RUNTIME"),
        (_server_error, 0, None),
    ],
    ids=["flood", "cooling", "connection", "auth-key", "server"],
)
def test_sync_media_rate_limit_still_advances_the_changes_cursor(
    config_env, monkeypatch, capsys, make_error, exit_code, code
):
    """The difference was applied, then media download hit a rate limit and
    the run exited 5 before the cursor checkpoint. Every run during the
    cooldown re-fetched and re-applied the same difference. The cursor is
    now saved before the media tail, and the media row keeps its attempts.

    The same holds for every error that is about the run, not the item: a
    dropped connection or a dead session aborts the run, and a Telegram 500
    stops the media pass for the next run. None of them used to be told
    apart from a broken file, so three of them made the row `no_media` for
    good."""
    user = _user()

    class CooldownClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=5, qts=1, seq=1)
            if isinstance(request, GetDifferenceRequest):
                return Difference(
                    new_messages=[_msg(mid=3, text="new text")],
                    new_encrypted_messages=[],
                    other_updates=[],
                    chats=[],
                    users=[user],
                    state=_state(pts=9, qts=1, seq=2),
                )
            raise AssertionError(request)

        async def get_messages(self, entity, ids=None, limit=None, reply_to=None):
            if ids is not None:
                raise make_error()
            return await super().get_messages(entity, ids, limit, reply_to)

    client = CooldownClient(me=_me(), entities={7: user, "7": user, "@alice": user})
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    path = archive_cmd.db_path("main")
    conn = store_mod.connect(path)
    try:
        store_mod.upsert_message(
            conn,
            7,
            {
                "id": 2,
                "date": "2026-01-02T00:00:00+00:00",
                "text": "pending voice",
                "from": {"id": 7},
                "media": "MessageMediaDocument",
                "media_info": {"mime": "audio/ogg", "size": 10},
                "media_kind": "voice",
            },
        )
        cursor = ChangesCursor(pts=5, qts=1, date=1_735_689_600, seq=1, channels={})
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
        conn.commit()
    finally:
        conn.close()

    assert main(["archive", "sync", "--json"]) == exit_code
    captured = capsys.readouterr()
    if code is None:
        media = json.loads(captured.out)["media"]
        assert media["failed"] == []
        assert media["remaining"] is True
    else:
        envelope = json.loads(captured.out)
        assert envelope["error"]["code"] == code
        assert json.loads(captured.err.strip().splitlines()[-1]) == envelope

    conn = store_mod.connect(path)
    try:
        saved = store_mod.read_account_sync(conn)["changes_cursor"]
        assert changes_cursor.decode(saved).pts == 9
        new_row = conn.execute(
            "SELECT text FROM messages WHERE peer_id = 7 AND message_id = 3"
        ).fetchone()
        assert new_row["text"] == "new text"
        row = store_mod.transcript_row(conn, 7, 2)
        assert row["media_attempts"] == 0
        assert row["media_status"] == "pending"
    finally:
        conn.close()


def test_voice_whose_media_never_downloads_leaves_the_transcript_queue(
    config_env, monkeypatch, capsys
):
    """A voice message whose media failed the attempt cap became `no_media`
    but kept transcript status `pending`. Transcribe only takes rows with
    media, so the row was never drained and `status` counted it in the
    transcript queue forever."""
    user = _user()

    class EmptyDifferenceClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetStateRequest):
                return _state(pts=5, qts=1, seq=1)
            if isinstance(request, GetDifferenceRequest):
                return DifferenceEmpty(date=datetime(2026, 1, 5, tzinfo=UTC), seq=1)
            raise AssertionError(request)

    client = EmptyDifferenceClient(
        me=_me(), entities={7: user, "7": user, "@alice": user}
    )
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        store_mod.upsert_message(
            conn,
            7,
            {
                "id": 2,
                "date": "2026-01-02T00:00:00+00:00",
                "text": "deleted voice",
                "from": {"id": 7},
                "media": "MessageMediaDocument",
                "media_info": {"mime": "audio/ogg", "size": 10},
                "media_kind": "voice",
            },
        )
        cursor = ChangesCursor(pts=5, qts=1, date=1_735_689_600, seq=1, channels={})
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
        conn.commit()
    finally:
        conn.close()

    for _ in range(3):
        assert main(["archive", "sync", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["media"]["failed"]

    assert main(["archive", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["transcript_queue"] == 0
    assert status["transcript_status"] == {
        "pending": 0,
        "retryable": 0,
        "done": 0,
        "no_transcript": 1,
    }
    [error] = status["transcript_errors"]
    assert (error["status"], error["attempts"], error["media_status"]) == (
        "no_transcript",
        0,
        "no_media",
    )

    assert main(["archive", "search", "no_transcript", "--json"]) == 0
    [hit] = json.loads(capsys.readouterr().out)["hits"]
    assert (hit["message_id"], hit["transcript_status"]) == (2, "no_transcript")

    assert main(["archive", "transcribe", "--json"]) == 0
    transcribe = json.loads(capsys.readouterr().out)
    assert (transcribe["queued"], transcribe["remaining"]) == (0, False)


def test_sync_catch_up_of_a_channel_without_history_keeps_its_newest_page(
    config_env, monkeypatch, capsys
):
    """A scoped channel that was never backfilled has no `newest_id`. Reading
    it oldest first would store the channel's first messages and crawl its
    whole history one page per activity, so it keeps the newest page."""
    from datetime import timedelta

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

    class ActivityClient(FakeClient):
        async def __call__(self, request):
            self.call_requests.append(request)
            if isinstance(request, GetFullChannelRequest):
                # Skip subscription so UpdateChannelTooLong stays visible.
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

    newest_first = [
        Message(
            id=message_id,
            peer_id=PeerChannel(1234),
            message=f"m{message_id}",
            date=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=message_id),
        )
        for message_id in range(200, 0, -1)
    ]
    client = ActivityClient(
        me=_me(),
        entities={peer: channel, 1234: channel, "@news": channel, "news": channel},
        messages=newest_first,
        message_total=200,
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
        cursor = ChangesCursor(pts=5, qts=1, date=1_735_689_600, seq=1, channels={})
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
        conn.commit()
    finally:
        conn.close()

    assert main(["archive", "sync", "--json"]) == 0
    captured = capsys.readouterr()
    assert '"error"' not in captured.err
    data = json.loads(captured.out)
    assert [item["peer_id"] for item in data["catchups"]] == [peer]
    assert data["catchups"][0]["stored"] == sync_mod.CATCHUP_LIMIT
    assert data["catchups"][0]["more"] is False
    assert client.iter_messages_reverse_calls == [False]

    conn = store_mod.connect(path)
    try:
        stored = {
            int(row[0])
            for row in conn.execute(
                "SELECT message_id FROM messages WHERE peer_id = ?", (peer,)
            )
        }
        assert stored == set(range(200 - sync_mod.CATCHUP_LIMIT + 1, 201))
        assert store_mod.get_sync_state(conn, peer)["newest_id"] == 200
    finally:
        conn.close()
