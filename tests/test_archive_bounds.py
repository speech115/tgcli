"""Characterization of every `tg archive` numeric bound at the CLI seam.

Each bounded flag has a default, a hard cap, and two exact refusals (exit 2):
"must be positive" and "accepts at most N". The JSON a command prints echoes
the value it ran with, so defaults and caps are pinned there.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from telethon.tl.functions.updates import GetDifferenceRequest, GetStateRequest
from telethon.tl.types.updates import DifferenceEmpty, State

from tests.conftest import FakeClient, make_session_fake
from tests.test_cli_archive import _client, _me, _seed_search_corpus, _user
from tgcli import changes_cursor
from tgcli.archive import store as store_mod
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


_TWENTY_ONE_CHATS = [f"@c{i}" for i in range(21)]

REFUSALS = [
    (
        ["backfill", "@alice", "--limit", "0"],
        "archive backfill --limit must be positive",
    ),
    (
        ["backfill", "@alice", "--limit", "-3"],
        "archive backfill --limit must be positive",
    ),
    (
        ["backfill", "@alice", "--limit", "1001"],
        "archive backfill --limit accepts at most 1000 messages per dialog",
    ),
    (
        ["backfill", "--private", "--max-dialogs", "0"],
        "archive backfill --max-dialogs must be positive",
    ),
    (
        ["backfill", "--private", "--max-dialogs", "101"],
        "archive backfill --max-dialogs accepts at most 100 dialogs",
    ),
    (
        ["backfill", *_TWENTY_ONE_CHATS],
        "archive backfill accepts at most 20 dialogs per invocation",
    ),
    (["sync", "--max-events", "0"], "archive sync --max-events must be positive"),
    (
        ["sync", "--max-events", "5001"],
        "archive sync --max-events accepts at most 5000",
    ),
    (["sync", "--max-dialogs", "0"], "archive sync --max-dialogs must be positive"),
    (
        ["sync", "--max-dialogs", "51"],
        "archive sync --max-dialogs accepts at most 50",
    ),
    (["sync", "--max-media", "-1"], "archive sync --max-media must be positive"),
    (["sync", "--max-media", "501"], "archive sync --max-media accepts at most 500"),
    (["search", "x", "--limit", "0"], "archive search --limit must be positive"),
    (
        ["search", "x", "--limit", "51"],
        "archive search --limit accepts at most 50 hits",
    ),
    (["search", "x", "--page", "0"], "archive search --page must be positive"),
    (
        ["search", "x", "--page", "10001"],
        "archive search --page accepts at most 10000",
    ),
    (["read", "@alice", "--limit", "-1"], "archive read --limit must be positive"),
    (
        ["read", "@alice", "--limit", "51"],
        "archive read --limit accepts at most 50 hits",
    ),
    (["transcribe", "--limit", "0"], "archive transcribe --limit must be positive"),
    (
        ["transcribe", "--limit", "101"],
        "archive transcribe --limit accepts at most 100",
    ),
    (
        ["transcribe", "--max-attempts", "0"],
        "archive transcribe --max-attempts must be positive",
    ),
    (
        ["transcribe", "--max-attempts", "6"],
        "archive transcribe --max-attempts accepts at most 5",
    ),
    # When several bounds fail, the first one checked is the one reported.
    (
        ["sync", "--max-events", "0", "--max-dialogs", "0", "--max-media", "0"],
        "archive sync --max-dialogs must be positive",
    ),
    (
        ["sync", "--max-events", "0", "--max-media", "0"],
        "archive sync --max-events must be positive",
    ),
    (
        ["search", "x", "--limit", "0", "--page", "0"],
        "archive search --limit must be positive",
    ),
    (
        ["transcribe", "--limit", "0", "--max-attempts", "0"],
        "archive transcribe --limit must be positive",
    ),
    (
        ["backfill", "--private", "--limit", "0", "--max-dialogs", "0"],
        "archive backfill --limit must be positive",
    ),
    (
        ["backfill", *_TWENTY_ONE_CHATS, "--limit", "0"],
        "archive backfill accepts at most 20 dialogs per invocation",
    ),
]


@pytest.mark.parametrize(
    ("argv", "message"), REFUSALS, ids=[" ".join(a[:4]) for a, _ in REFUSALS]
)
def test_archive_bound_refusals_are_exact_and_exit_2(config_env, capsys, argv, message):
    assert main(["archive", *argv, "--json"]) == 2
    captured = capsys.readouterr()
    error = json.loads(captured.out)["error"]
    assert error["code"] == "BLOCKED"
    assert error["message"] == message
    assert captured.err.splitlines()[-1] == captured.out.strip()


@pytest.mark.parametrize(
    ("extra", "limit", "page"),
    [([], 20, 1), (["--limit", "50", "--page", "10000"], 50, 10000)],
)
def test_archive_search_default_and_cap(
    config_env, monkeypatch, capsys, extra, limit, page
):
    _seed_search_corpus(monkeypatch)
    capsys.readouterr()
    assert main(["archive", "search", "ёлка", *extra, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["limit"], data["page"]) == (limit, page)


@pytest.mark.parametrize(("extra", "limit"), [([], 20), (["--limit", "50"], 50)])
def test_archive_read_default_and_cap(config_env, monkeypatch, capsys, extra, limit):
    _seed_search_corpus(monkeypatch)
    capsys.readouterr()
    assert main(["archive", "read", "7", *extra, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["limit"] == limit


@pytest.mark.parametrize(
    ("extra", "limit", "attempts"),
    [([], 20, 3), (["--limit", "100", "--max-attempts", "5"], 100, 5)],
)
def test_archive_transcribe_default_and_cap(
    config_env, monkeypatch, capsys, extra, limit, attempts
):
    make_session_fake(monkeypatch, _client())
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "transcribe", *extra, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["limit"], data["max_attempts"]) == (limit, attempts)


@pytest.mark.parametrize(("extra", "limit"), [([], 100), (["--limit", "1000"], 1000)])
def test_archive_backfill_chats_default_and_cap(
    config_env, monkeypatch, capsys, extra, limit
):
    alice = _user()
    make_session_fake(monkeypatch, _client(entities={"@alice": alice, 7: alice}))
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "backfill", "@alice", *extra, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["limit"] == limit


@pytest.mark.parametrize(
    ("extra", "limit", "max_dialogs"),
    [([], 100, 20), (["--limit", "1000", "--max-dialogs", "100"], 1000, 100)],
)
def test_archive_backfill_private_default_and_cap(
    config_env, monkeypatch, capsys, extra, limit, max_dialogs
):
    make_session_fake(monkeypatch, _client())
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    assert main(["archive", "backfill", "--private", *extra, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["limit"], data["max_dialogs"]) == (limit, max_dialogs)


class _QuietClient(FakeClient):
    async def __call__(self, request):
        self.call_requests.append(request)
        if isinstance(request, GetStateRequest):
            return State(
                pts=10,
                qts=1,
                date=datetime(2026, 1, 1, tzinfo=UTC),
                seq=2,
                unread_count=0,
            )
        if isinstance(request, GetDifferenceRequest):
            return DifferenceEmpty(date=datetime(2026, 1, 5, tzinfo=UTC), seq=2)
        raise AssertionError(request)


@pytest.mark.parametrize(
    ("extra", "budgets"),
    [
        ([], (500, 20, 50)),
        (
            ["--max-events", "5000", "--max-dialogs", "50", "--max-media", "500"],
            (5000, 50, 500),
        ),
    ],
)
def test_archive_sync_default_and_cap(config_env, monkeypatch, capsys, extra, budgets):
    make_session_fake(monkeypatch, _QuietClient(me=_me()))
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    cursor = ChangesCursor(pts=5, qts=1, date=1_735_689_600, seq=1, channels={})
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
    finally:
        conn.close()
    assert main(["archive", "sync", *extra, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["max_events"], data["max_dialogs"], data["max_media"]) == budgets
