"""Phase 5 archive search and exploration boundary tests (ADR-0068)."""

from __future__ import annotations

import json

import pytest

from tests.conftest import make_session_fake
from tests.test_cli_archive import _block_telegram_session, _client
from tgcli.archive import store as store_mod
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


def _seed_phase5_corpus(monkeypatch):
    make_session_fake(monkeypatch, _client())
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
        messages = [
            (
                7,
                1,
                "2026-01-01T00:00:00+00:00",
                "common old",
                7,
                "Alice",
                "alice",
                None,
            ),
            (
                7,
                2,
                "2026-01-03T00:00:00+00:00",
                "common common voice",
                7,
                "Alice",
                "alice",
                "voice",
            ),
            (
                -1001234,
                10,
                "2026-01-04T00:00:00+00:00",
                "common channel",
                9,
                "Bob",
                "bob",
                "video_note",
            ),
        ]
        for peer, mid, date, text, sender, name, username, kind in messages:
            payload = {
                "id": mid,
                "date": date,
                "from": {"id": sender, "name": name, "username": username},
                "text": text,
                "media_kind": kind,
                "media": "MessageMediaDocument" if kind else None,
                "permalink": None,
            }
            store_mod.upsert_message(conn, peer, payload, media_kind=kind)
        store_mod.ensure_transcript_queue(conn, 7, 2, media_kind="voice")
        store_mod.record_transcript_success(
            conn,
            7,
            2,
            text="russian transcript needle",
            model="parakeet",
            model_version="1",
        )
        store_mod.upsert_sync_state(
            conn,
            7,
            oldest_id=1,
            newest_id=2,
            more=True,
            chat_ref="@alice",
            title="Alice",
            username="alice",
            kind="user",
        )
        store_mod.upsert_sync_state(
            conn,
            -1001234,
            oldest_id=10,
            newest_id=10,
            more=False,
            chat_ref="@news",
            title="News",
            username="news",
            kind="channel",
        )
        conn.commit()
    finally:
        conn.close()


def test_archive_search_filters_ranking_paging_and_handoff(
    config_env, monkeypatch, capsys
):
    _seed_phase5_corpus(monkeypatch)
    capsys.readouterr()
    _block_telegram_session(monkeypatch)

    assert (
        main(
            [
                "archive",
                "search",
                "common",
                "--from",
                "@alice",
                "--kind",
                "voice",
                "--since",
                "2026-01-02",
                "--until",
                "2026-01-04",
                "--limit",
                "1",
                "--page",
                "1",
                "--json",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert [(hit["peer_id"], hit["message_id"]) for hit in data["hits"]] == [(7, 2)]
    hit = data["hits"][0]
    assert hit["tg_link"].startswith("tg://")
    assert hit["tg_link"] == "tg://openmessage?chat_id=7&message_id=2"
    assert hit["match_fields"] == ["text"]
    assert data["has_more"] is False

    assert main(["archive", "search", "needle", "--transcripts-only", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["hits"]) == 1
    assert data["hits"][0]["match_fields"] == ["transcript"]
    assert "needle" in data["hits"][0]["snippet"]
    assert data["hits"][0]["snippet_source"] == "transcript"

    assert (
        main(["archive", "search", "common", "--limit", "1", "--page", "1", "--json"])
        == 0
    )
    first = json.loads(capsys.readouterr().out)
    assert first["has_more"] is True
    assert first["next_page"] == 2
    assert (
        main(["archive", "search", "common", "--limit", "1", "--page", "2", "--json"])
        == 0
    )
    second = json.loads(capsys.readouterr().out)
    assert second["page"] == 2
    assert second["hits"][0]["message_id"] != first["hits"][0]["message_id"]

    assert main(["archive", "search", "common", "--sort", "date", "--json"]) == 0
    dated = json.loads(capsys.readouterr().out)
    assert [hit["message_id"] for hit in dated["hits"]] == [10, 2, 1]


def test_archive_search_rejects_invalid_phase5_filters(config_env, monkeypatch, capsys):
    _seed_phase5_corpus(monkeypatch)
    capsys.readouterr()
    for argv, text in (
        (["--from", ""], "from"),
        (["--since", "2026-01-04", "--until", "2026-01-01"], "since"),
        (["--page", "0"], "page"),
    ):
        assert main(["archive", "search", "common", *argv, "--json"]) == 2
        assert text in capsys.readouterr().err.lower()


def test_archive_read_is_offline_and_can_center_on_id_or_date(
    config_env, monkeypatch, capsys
):
    _seed_phase5_corpus(monkeypatch)
    capsys.readouterr()
    _block_telegram_session(monkeypatch)

    assert (
        main(
            ["archive", "read", "@alice", "--around-id", "2", "--limit", "2", "--json"]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["peer_id"] == 7
    assert [message["id"] for message in data["messages"]] == [1, 2]
    assert data["messages"][1]["tg_link"].startswith("tg://")
    assert data["scope"]["stale"] is True

    assert (
        main(
            [
                "archive",
                "read",
                "@alice",
                "--around-date",
                "2026-01-02",
                "--since",
                "2026-01-02",
                "--json",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert [message["id"] for message in data["messages"]] == [2]


def test_archive_history_exposes_revisions_and_tombstone(
    config_env, monkeypatch, capsys
):
    _seed_phase5_corpus(monkeypatch)
    capsys.readouterr()
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        store_mod.upsert_message(
            conn,
            7,
            {
                "id": 2,
                "date": "2026-01-03T00:00:00+00:00",
                "from": {"id": 7, "name": "Alice", "username": "alice"},
                "text": "edited current",
                "media_kind": "voice",
            },
            media_kind="voice",
        )
        store_mod.insert_tombstone(conn, 7, 2, deleted_at="2026-01-05T00:00:00+00:00")
        conn.commit()
    finally:
        conn.close()
    _block_telegram_session(monkeypatch)

    assert main(["archive", "history", "@alice", "2", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "deleted"
    assert data["current"]["text"] == "edited current"
    assert data["revisions"][0]["message"]["text"] == "common common voice"
    assert data["tombstone"]["deleted_at"] == "2026-01-05T00:00:00+00:00"
    assert data["current"]["tg_link"].startswith("tg://")


def test_archive_read_and_history_validate_caps_and_readonly(
    config_env, monkeypatch, capsys
):
    _seed_phase5_corpus(monkeypatch)
    capsys.readouterr()
    assert main(["archive", "read", "@alice", "--limit", "0", "--json"]) == 2
    assert "limit" in capsys.readouterr().err.lower()
    assert (
        main(
            [
                "archive",
                "read",
                "@alice",
                "--around-id",
                "1",
                "--around-date",
                "2026-01-01",
                "--json",
            ]
        )
        == 2
    )
    assert "around" in capsys.readouterr().err.lower()
    assert main(["archive", "history", "@alice", "0", "--json"]) == 2
    assert "message" in capsys.readouterr().err.lower()
    assert main(["--readonly", "archive", "read", "@alice", "--json"]) == 0
    capsys.readouterr()
