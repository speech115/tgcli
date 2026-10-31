"""A peer that left archive scope keeps its data but no longer drives sync.

`archive remove` keeps the peer's messages and `sync_state` row so a later
`add` resumes. Reconciliation and explore staleness must still ignore it:
only private users and explicit scope rows are in scope.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from telethon import utils
from telethon.tl.functions.updates import GetDifferenceRequest, GetStateRequest
from telethon.tl.types import Channel, Message, PeerUser, User
from telethon.tl.types.updates import DifferenceEmpty, State

from tests.conftest import FakeClient, make_session_fake
from tgcli.cli import main

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
session = "main"
"""


def _setup_config(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))
    state = tmp_path / "state"
    monkeypatch.setenv("TGCLI_STATE_DIR", str(state))
    (state / "sessions").mkdir(parents=True, exist_ok=True)
    (state / "sessions" / "main.session").write_bytes(b"x")


def _state() -> State:
    return State(
        pts=5,
        qts=1,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        seq=1,
        unread_count=0,
    )


class QuietClient(FakeClient):
    async def __call__(self, request):
        self.call_requests.append(request)
        if isinstance(request, GetStateRequest):
            return _state()
        if isinstance(request, GetDifferenceRequest):
            return DifferenceEmpty(date=datetime(2026, 1, 5, tzinfo=UTC), seq=1)
        raise AssertionError(request)


def _run(*argv: str, capsys) -> dict:
    assert main([*argv, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_removed_channel_is_not_reconciled_and_does_not_make_search_stale(
    tmp_path, monkeypatch, capsys
):
    _setup_config(tmp_path, monkeypatch)
    alice = User(
        id=7, is_self=False, access_hash=2, first_name="Alice", username="alice"
    )
    news = Channel(
        id=1234,
        title="News",
        photo=None,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        access_hash=99,
        megagroup=False,
        username="news",
    )
    news_peer = utils.get_peer_id(news)
    client = QuietClient(
        me=User(id=42, is_self=True, access_hash=1, first_name="Me", phone="100"),
        entities={
            "@alice": alice,
            7: alice,
            "@news": news,
            news_peer: news,
            1234: news,
        },
        messages=[
            Message(
                id=mid,
                peer_id=PeerUser(7),
                message=f"hello {mid}",
                date=datetime(2026, 1, 2, tzinfo=UTC),
                out=False,
            )
            for mid in (3, 2, 1)
        ],
    )
    make_session_fake(monkeypatch, client)
    _run("archive", "init", capsys=capsys)
    _run("archive", "add", "@news", capsys=capsys)
    news_backfill = _run("archive", "backfill", "@news", "--limit", "2", capsys=capsys)
    assert news_backfill["dialogs"][0]["more"] is True
    alice_backfill = _run(
        "archive", "backfill", "@alice", "--limit", "10", capsys=capsys
    )
    assert alice_backfill["dialogs"][0]["more"] is False
    _run("archive", "remove", "@news", capsys=capsys)

    calls_before_sync = len(client.get_messages_calls)
    sync = _run("archive", "sync", capsys=capsys)

    assert [item["peer_id"] for item in sync["reconcile"]["comparisons"]] == [7]
    sync_calls = client.get_messages_calls[calls_before_sync:]
    assert [entity for entity, _ids, limit, _reply in sync_calls if limit == 0] == [
        alice
    ]

    search = _run("archive", "search", "hello", capsys=capsys)
    assert search["scope"]["stale"] is False

    status = _run("archive", "status", capsys=capsys)
    assert sorted(row["peer_id"] for row in status["dialogs"]) == [news_peer, 7]
