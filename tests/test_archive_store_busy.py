"""Archive store connection pragmas (thermos T24)."""

import sqlite3

from tgcli.archive import store as archive_store


def test_archive_connect_exposes_named_busy_timeout_policy(tmp_path):
    conn = archive_store.connect(tmp_path / "archive.db")
    try:
        assert (
            conn.execute("PRAGMA busy_timeout").fetchone()[0]
            == archive_store.BUSY_TIMEOUT_MS
        )
    finally:
        conn.close()


def test_archive_connect_issues_busy_timeout_pragma(tmp_path, monkeypatch):
    real_connect = sqlite3.connect
    statements: list[str] = []

    def traced_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr("tgcli.archive.schema.sqlite3.connect", traced_connect)
    conn = archive_store.connect(tmp_path / "archive.db")
    conn.close()

    assert f"PRAGMA busy_timeout = {archive_store.BUSY_TIMEOUT_MS}" in statements
