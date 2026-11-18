"""Archive Phase 4 store and transcription-boundary tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from tgcli.archive import (
    explore as explore_module,
    media as media_module,
    store,
    sync as sync_module,
    transcribe as transcribe_module,
)
from tgcli.errors import PolicyError


def _payload(message_id: int, text: str, *, media_kind: str = "voice") -> dict:
    return {
        "id": message_id,
        "date": f"2026-01-{message_id:02d}T00:00:00+00:00",
        "from": {"id": 7},
        "text": text,
        "media": "MessageMediaDocument",
        "media_info": {"mime": "audio/ogg", "size": 10},
        "media_kind": media_kind,
    }


def _connection(tmp_path):
    conn = store.connect(tmp_path / "archive.db")
    store.ensure_meta(conn, account_user_id=42, account_alias="main")
    return conn


def test_message_edit_rebuild_preserves_transcript_in_fts(tmp_path):
    conn = _connection(tmp_path)
    try:
        assert store.upsert_message(conn, 7, _payload(1, "caption")) == "inserted"
        media_module.set_media_path(
            conn,
            7,
            1,
            path="media/7/1.ogg",
            media_kind="voice",
        )
        store.record_transcript_success(
            conn,
            7,
            1,
            text="русский transcript",
            model="fluidaudio-parakeet-v3",
            model_version="v3",
        )

        edited = _payload(1, "edited caption")
        assert store.upsert_message(conn, 7, edited) == "updated"
        rows = conn.execute(
            "SELECT text, transcript FROM messages_fts WHERE messages_fts MATCH ?",
            (store.fold_yo("русский"),),
        ).fetchall()
        assert [(row["text"], row["transcript"]) for row in rows] == [
            ("edited caption", "русский transcript")
        ]
        assert store.transcript_row(conn, 7, 1)["text"] == "русский transcript"
    finally:
        conn.close()


def test_schema_v7_keeps_media_failure_state_without_refresh_state(tmp_path):
    conn = _connection(tmp_path)
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(transcripts)").fetchall()
        }
        assert {
            "media_path",
            "media_kind",
            "last_error",
            "media_attempts",
            "media_status",
        } <= columns
        refresh_columns = {
            "refresh_failure_streak",
            "refresh_last_error",
            "refresh_notification_sent",
        }
        assert refresh_columns.isdisjoint(
            {
                row[1]
                for row in conn.execute("PRAGMA table_info(account_sync)").fetchall()
            }
        )
        assert store.schema_version(conn) == 12
    finally:
        conn.close()


def test_media_relative_path_uses_controlled_suffixes():
    assert media_module.media_relative_path(7, 9, media_kind="voice") == "media/7/9.ogg"
    assert (
        media_module.media_relative_path(7, 9, media_kind="video_note")
        == "media/7/9.mp4"
    )
    assert media_module.media_relative_path(
        7, 9, media_kind="audio", mime="audio/mp4"
    ) == ("media/7/9.m4a")


@pytest.mark.asyncio
async def test_media_fetch_publishes_and_is_idempotent(tmp_path, monkeypatch):
    conn = _connection(tmp_path)
    account_dir = tmp_path / "account"
    account_dir.mkdir()
    try:
        store.upsert_message(conn, 7, _payload(1, "caption"))
        conn.commit()
        calls = []

        async def fake_download(_tg, source, alias, *, output, parallel):
            calls.append((source, alias, output, parallel))
            target = Path(output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"voice")
            return {"path": output, "bytes": 5}

        monkeypatch.setattr(sync_module.media_cmd, "download_media", fake_download)
        first = await sync_module.fetch_media(
            object(),
            conn,
            account_alias="main",
            account_user_id=42,
            account_dir=account_dir,
            limit=10,
        )
        assert first["limit"] == 10
        assert first["downloaded"] == 1
        assert len(calls) == 1
        assert calls[0][0].message_id == 1
        assert calls[0][1] == "main"
        assert calls[0][3] == 1
        assert store.transcript_row(conn, 7, 1)["media_path"] == "media/7/1.ogg"
        assert (account_dir / "media/7/1.ogg").read_bytes() == b"voice"

        second = await sync_module.fetch_media(
            object(),
            conn,
            account_alias="main",
            account_user_id=42,
            account_dir=account_dir,
            limit=10,
        )
        assert second["queued"] == 0
        assert len(calls) == 1
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_media_failure_retries_then_becomes_terminal(tmp_path, monkeypatch):
    conn = _connection(tmp_path)
    account_dir = tmp_path / "account"
    account_dir.mkdir()
    try:
        store.upsert_message(conn, 7, _payload(1, "caption"))
        conn.commit()
        calls = []

        async def failed_download(_tg, source, _alias, *, output, parallel):
            calls.append((source.message_id, output, parallel))
            raise RuntimeError("media is unavailable")

        monkeypatch.setattr(sync_module.media_cmd, "download_media", failed_download)
        for attempt in range(1, media_module.MAX_MEDIA_ATTEMPTS + 1):
            result = await sync_module.fetch_media(
                object(),
                conn,
                account_alias="main",
                account_user_id=42,
                account_dir=account_dir,
                limit=10,
            )
            row = store.transcript_row(conn, 7, 1)
            assert result["queued"] == 1
            assert result["failed"][0]["message_id"] == 1
            assert row["media_attempts"] == attempt
            assert row["media_status"] == (
                "no_media"
                if attempt == media_module.MAX_MEDIA_ATTEMPTS
                else "retryable"
            )
            assert result["remaining"] is (attempt < media_module.MAX_MEDIA_ATTEMPTS)

        result = await sync_module.fetch_media(
            object(),
            conn,
            account_alias="main",
            account_user_id=42,
            account_dir=account_dir,
            limit=10,
        )
        assert result["queued"] == 0
        assert len(calls) == media_module.MAX_MEDIA_ATTEMPTS
    finally:
        conn.close()


def test_transcribe_queue_stores_parakeet_text_and_metadata(tmp_path, monkeypatch):
    conn = _connection(tmp_path)
    media_dir = tmp_path / "account" / "media" / "7"
    media_dir.mkdir(parents=True)
    media_path = media_dir / "1.ogg"
    media_path.write_bytes(b"voice")
    try:
        store.upsert_message(conn, 7, _payload(1, "caption"))
        media_module.set_media_path(
            conn, 7, 1, path="media/7/1.ogg", media_kind="voice"
        )
        conn.commit()

        monkeypatch.setattr(
            transcribe_module.shutil, "which", lambda _: "/bin/transcribe"
        )

        def fake_run(argv, **_kwargs):
            output_dir = Path(argv[argv.index("--out") + 1])
            (output_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "engine": "fluidaudio-parakeet-v3",
                        "asr_model": "v3",
                    }
                )
            )
            (output_dir / "transcript.json").write_text(
                json.dumps(
                    {
                        "turns": [
                            {"text": "первая реплика"},
                            {"text": "вторая реплика"},
                        ]
                    }
                )
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(transcribe_module.subprocess, "run", fake_run)
        result = transcribe_module.run_queue(
            conn,
            tmp_path / "account",
            limit=20,
            max_attempts=3,
        )
        assert result["transcribed"] == 1
        row = store.transcript_row(conn, 7, 1)
        assert row["status"] == "done"
        assert row["text"] == "первая реплика\nвторая реплика"
        assert row["model"] == "fluidaudio-parakeet-v3"
        assert row["model_version"] == "v3"
        hits = conn.execute(
            "SELECT peer_id, message_id FROM messages_fts WHERE messages_fts MATCH ?",
            (store.fold_yo("вторая"),),
        ).fetchall()
        assert [(row["peer_id"], row["message_id"]) for row in hits] == [(7, 1)]
        result = explore_module.search(conn, "вторая")
        assert result["hits"][0]["transcript"] == "первая реплика\nвторая реплика"
        assert result["hits"][0]["transcript_status"] == "done"
    finally:
        conn.close()


def test_transcribe_row_exhausted_under_a_lower_cap_becomes_terminal(
    tmp_path, monkeypatch
):
    """Three failures under `--max-attempts 5` left a row
    `retryable`. A run at the default cap of 3 never queued it again, so it
    stayed `retryable` for good instead of becoming `no_transcript`."""
    conn = _connection(tmp_path)
    media_dir = tmp_path / "account" / "media" / "7"
    media_dir.mkdir(parents=True)
    (media_dir / "1.ogg").write_bytes(b"voice")
    try:
        store.upsert_message(conn, 7, _payload(1, "caption"))
        media_module.set_media_path(
            conn, 7, 1, path="media/7/1.ogg", media_kind="voice"
        )
        conn.commit()
        monkeypatch.setattr(
            transcribe_module.shutil, "which", lambda _: "/bin/transcribe"
        )
        monkeypatch.setattr(
            transcribe_module.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=1, stdout="", stderr="temporary engine failure"
            ),
        )
        for _ in range(3):
            transcribe_module.run_queue(
                conn, tmp_path / "account", limit=20, max_attempts=5
            )
        assert store.transcript_row(conn, 7, 1)["status"] == "retryable"

        result = transcribe_module.run_queue(
            conn, tmp_path / "account", limit=20, max_attempts=3
        )

        row = store.transcript_row(conn, 7, 1)
        assert row["status"] == "no_transcript"
        assert row["attempts"] == 3
        assert "temporary engine failure" in row["last_error"]
        assert result["remaining"] is False
        hits = explore_module.search(conn, "no_transcript")["hits"]
        assert hits[0]["transcript_status"] == "no_transcript"
        caption_hits = conn.execute(
            "SELECT peer_id, message_id FROM messages_fts WHERE messages_fts MATCH ?",
            ("caption",),
        ).fetchall()
        assert [(r["peer_id"], r["message_id"]) for r in caption_hits] == [(7, 1)]
    finally:
        conn.close()


def test_transcribe_retryable_failure_becomes_terminal_at_cap(tmp_path, monkeypatch):
    conn = _connection(tmp_path)
    media_dir = tmp_path / "account" / "media" / "7"
    media_dir.mkdir(parents=True)
    (media_dir / "1.ogg").write_bytes(b"voice")
    try:
        store.upsert_message(conn, 7, _payload(1, "caption"))
        media_module.set_media_path(
            conn, 7, 1, path="media/7/1.ogg", media_kind="voice"
        )
        conn.commit()
        monkeypatch.setattr(
            transcribe_module.shutil, "which", lambda _: "/bin/transcribe"
        )
        monkeypatch.setattr(
            transcribe_module.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=1, stdout="", stderr="temporary engine failure"
            ),
        )
        result = transcribe_module.run_queue(
            conn,
            tmp_path / "account",
            limit=20,
            max_attempts=1,
        )
        assert result["retryable"] == 0
        assert result["no_transcript"] == 1
        row = store.transcript_row(conn, 7, 1)
        assert row["status"] == "no_transcript"
        assert row["attempts"] == 1
        assert "temporary engine failure" in row["last_error"]
        assert store.transcript_status_counts(conn) == {
            "pending": 0,
            "retryable": 0,
            "done": 0,
            "no_transcript": 1,
        }
        errors = store.transcript_errors(conn)
        assert errors[0]["status"] == "no_transcript"
        result = explore_module.search(conn, "no_transcript")
        assert result["hits"][0]["transcript_status"] == "no_transcript"
    finally:
        conn.close()


def test_transcribe_stops_between_rows_at_the_wall_clock_cap(tmp_path, monkeypatch):
    conn = _connection(tmp_path)
    media_dir = tmp_path / "account" / "media" / "7"
    media_dir.mkdir(parents=True)
    try:
        for message_id in (1, 2):
            (media_dir / f"{message_id}.ogg").write_bytes(b"voice")
            store.upsert_message(conn, 7, _payload(message_id, "caption"))
            media_module.set_media_path(
                conn,
                7,
                message_id,
                path=f"media/7/{message_id}.ogg",
                media_kind="voice",
            )
        conn.commit()
        monkeypatch.setattr(
            transcribe_module.shutil, "which", lambda _: "/bin/transcribe"
        )
        runs = []

        def fake_run(argv, **_kwargs):
            runs.append(argv)
            output_dir = Path(argv[argv.index("--out") + 1])
            (output_dir / "manifest.json").write_text('{"engine": "parakeet-v3"}')
            (output_dir / "transcript.json").write_text('{"turns": [{"text": "t"}]}')
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(transcribe_module.subprocess, "run", fake_run)
        result = transcribe_module.run_queue(
            conn,
            tmp_path / "account",
            limit=20,
            max_attempts=3,
            should_stop=lambda: len(runs) >= 1,
        )
        assert len(runs) == 1
        assert result["transcribed"] == 1
        assert result["remaining"] is True
        assert result["stop_reason"] == "wall_clock_cap"
    finally:
        conn.close()


def test_transcribe_missing_media_returns_item_to_media_queue(tmp_path, monkeypatch):
    conn = _connection(tmp_path)
    account_dir = tmp_path / "account"
    account_dir.mkdir()
    try:
        store.upsert_message(conn, 7, _payload(1, "caption"))
        media_module.set_media_path(
            conn, 7, 1, path="media/7/1.ogg", media_kind="voice"
        )
        conn.commit()
        monkeypatch.setattr(
            transcribe_module.shutil, "which", lambda _: "/bin/transcribe"
        )
        result = transcribe_module.run_queue(
            conn,
            account_dir,
            limit=20,
            max_attempts=3,
        )
        assert result["skipped_missing_media"] == 1
        row = store.transcript_row(conn, 7, 1)
        assert row["media_path"] is None
        assert row["status"] == "pending"
        assert row["attempts"] == 0
        assert row["media_attempts"] == 1
        assert row["media_status"] == "retryable"
        assert store.list_transcript_queue(conn, limit=20, max_attempts=3) == []
        assert sync_module._media_candidates(conn, account_dir, 1)[0]["message_id"] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("version", [1, 11, store.SCHEMA_VERSION + 1])
def test_a_store_at_another_schema_version_is_refused(tmp_path, version):
    """Only the current schema opens; the migrations to it are gone."""
    path = tmp_path / "archive.db"
    store.connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version={version}")
    raw.commit()
    raw.close()

    with pytest.raises(PolicyError, match="unsupported schema version"):
        store.connect(path)
