"""Archive store connection pragmas (thermos T24)."""

from tgcli.archive import store as archive_store


def test_archive_connect_sets_busy_timeout(tmp_path):
    conn = archive_store.connect(tmp_path / "archive.db")
    try:
        assert (
            conn.execute("PRAGMA busy_timeout").fetchone()[0]
            == archive_store.BUSY_TIMEOUT_MS
        )
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()
