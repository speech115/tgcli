"""Characterization of the two archive state write seams, by stored row.

`upsert_sync_state` (per peer) and `write_account_sync` (the account
singleton) are what every backfill and sync run writes through. These tests
pin the exact raw rows they leave behind, column by column, under a fixed
clock, so a rewrite of either seam cannot change stored data.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from tgcli.archive import (
    changes_cursor as cursor_codec,
    store as store_mod,
    sync_state as sync_state_mod,
)
from tgcli.archive.changes_cursor import ChangesCursor

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _ts(n: int) -> str:
    return (T0 + timedelta(seconds=n)).isoformat()


@pytest.fixture
def clock(monkeypatch):
    """Each `datetime.now()` in the write seam returns the next second."""
    ticks = {"n": 0}

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            ticks["n"] += 1
            return T0 + timedelta(seconds=ticks["n"])

    monkeypatch.setattr(sync_state_mod, "datetime", _Clock)
    return ticks


@pytest.fixture
def conn(tmp_path):
    connection = store_mod.connect(tmp_path / "archive.db")
    yield connection
    connection.close()


def _peer_row(conn: sqlite3.Connection, peer_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM sync_state WHERE peer_id = ?", (peer_id,)
    ).fetchone()
    return dict(row)


def _account_row(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM account_sync WHERE id = 1").fetchone()
    return None if row is None else dict(row)


def _cursor(**channels: int) -> str:
    return cursor_codec.encode(
        ChangesCursor(
            pts=5,
            qts=1,
            date=1_735_689_600,
            seq=1,
            channels={int(peer): pts for peer, pts in channels.items()},
        )
    )


def test_upsert_sync_state_rows(conn, clock):
    upsert = store_mod.upsert_sync_state

    # Insert, backfill flavour: only last_backfill_at is stamped.
    upsert(
        conn,
        7,
        oldest_id=10,
        newest_id=20,
        more=True,
        last_error="FLOOD_WAIT:5",
        kind="user",
        title="Alice",
        username="alice",
        chat_ref="@alice",
    )
    assert _peer_row(conn, 7) == {
        "peer_id": 7,
        "oldest_id": 10,
        "newest_id": 20,
        "last_backfill_at": _ts(1),
        "last_sync_at": None,
        "last_error": "FLOOD_WAIT:5",
        "more": 1,
        "kind": "user",
        "title": "Alice",
        "username": "alice",
        "chat_ref": "@alice",
    }

    # Update, sync flavour: ids widen only, identity is kept when not given,
    # last_error and more are always overwritten.
    upsert(conn, 7, oldest_id=15, newest_id=30, more=False, touch_sync=True)
    assert _peer_row(conn, 7) == {
        "peer_id": 7,
        "oldest_id": 10,
        "newest_id": 30,
        "last_backfill_at": _ts(1),
        "last_sync_at": _ts(2),
        "last_error": None,
        "more": 0,
        "kind": "user",
        "title": "Alice",
        "username": "alice",
        "chat_ref": "@alice",
    }

    # Lower oldest, lower newest, new identity wins; backfill stamp moves.
    upsert(
        conn,
        7,
        oldest_id=3,
        newest_id=25,
        more=True,
        last_error="x",
        title="Alice B",
        chat_ref="7",
    )
    assert _peer_row(conn, 7) == {
        "peer_id": 7,
        "oldest_id": 3,
        "newest_id": 30,
        "last_backfill_at": _ts(3),
        "last_sync_at": _ts(2),
        "last_error": "x",
        "more": 1,
        "kind": "user",
        "title": "Alice B",
        "username": "alice",
        "chat_ref": "7",
    }

    # None ids keep the stored range.
    upsert(conn, 7, oldest_id=None, newest_id=None, more=False, touch_sync=True)
    row = _peer_row(conn, 7)
    assert (row["oldest_id"], row["newest_id"], row["last_sync_at"]) == (3, 30, _ts(4))
    assert row["last_backfill_at"] == _ts(3)


def test_upsert_sync_state_fills_an_empty_range_and_sync_insert(conn, clock):
    upsert = store_mod.upsert_sync_state

    # Insert, sync flavour, without a range or identity.
    upsert(conn, -100, oldest_id=None, newest_id=None, more=False, touch_sync=True)
    assert _peer_row(conn, -100) == {
        "peer_id": -100,
        "oldest_id": None,
        "newest_id": None,
        "last_backfill_at": None,
        "last_sync_at": _ts(1),
        "last_error": None,
        "more": 0,
        "kind": None,
        "title": None,
        "username": None,
        "chat_ref": None,
    }

    # A stored NULL range takes the new ids as they are.
    upsert(conn, -100, oldest_id=40, newest_id=50, more=True, kind="channel")
    row = _peer_row(conn, -100)
    assert (row["oldest_id"], row["newest_id"], row["more"]) == (40, 50, 1)
    assert (row["last_backfill_at"], row["last_sync_at"]) == (_ts(2), _ts(1))
    assert row["kind"] == "channel"

    # Only one side given: the other side is kept.
    upsert(conn, -100, oldest_id=None, newest_id=60, more=True)
    upsert(conn, -100, oldest_id=35, newest_id=None, more=True)
    row = _peer_row(conn, -100)
    assert (row["oldest_id"], row["newest_id"]) == (35, 60)


def test_write_account_sync_rows(conn, clock):
    write = store_mod.write_account_sync
    assert _account_row(conn) is None

    # The first write creates the singleton even with nothing to set.
    assert write(conn) is None
    assert _account_row(conn) == {
        "id": 1,
        "changes_cursor": None,
        "gap_json": None,
        "last_sync_at": None,
        "last_reconcile_at": None,
        "reconcile_json": None,
        "private_enum_json": None,
        "pending_catchups_json": None,
    }

    first = _cursor()
    assert (
        write(
            conn,
            changes_cursor=first,
            gap={"reason": "différenceTooLong", "pts": 50},
            touch_sync=True,
            pending_catchups=[-1003, -1001],
        )
        == first
    )
    assert _account_row(conn) == {
        "id": 1,
        "changes_cursor": first,
        "gap_json": '{"reason":"différenceTooLong","pts":50}',
        "last_sync_at": _ts(2),
        "last_reconcile_at": None,
        "reconcile_json": None,
        "private_enum_json": None,
        "pending_catchups_json": "[-1003, -1001]",
    }

    # Reconcile stamps its own time and leaves everything else alone.
    assert write(conn, reconcile={"next_offset": 3, "checked": []}) == first
    after_reconcile = {
        "id": 1,
        "changes_cursor": first,
        "gap_json": '{"reason":"différenceTooLong","pts":50}',
        "last_sync_at": _ts(2),
        "last_reconcile_at": _ts(3),
        "reconcile_json": '{"next_offset":3,"checked":[]}',
        "private_enum_json": None,
        "pending_catchups_json": "[-1003, -1001]",
    }
    assert _account_row(conn) == after_reconcile

    # Private-enum resume is set, then cleared; nothing else moves.
    write(conn, private_enum={"id": 7, "kind": "user", "offset_date": None})
    assert _account_row(conn) == {
        **after_reconcile,
        "private_enum_json": '{"id":7,"kind":"user","offset_date":null}',
    }
    write(conn, clear_private_enum=True)
    assert _account_row(conn) == after_reconcile

    # An empty pending list is stored as NULL; a later gap replaces the old.
    write(conn, pending_catchups=[], gap={"reason": "channel"})
    assert _account_row(conn) == {
        **after_reconcile,
        "gap_json": '{"reason":"channel"}',
        "pending_catchups_json": None,
    }

    # clear_gap wins over a gap passed in the same call.
    write(conn, gap={"reason": "ignored"}, clear_gap=True, touch_sync=True)
    assert _account_row(conn) == {
        **after_reconcile,
        "gap_json": None,
        "last_sync_at": _ts(7),
        "pending_catchups_json": None,
    }


def test_write_account_sync_scopes_cursor_channels(conn, clock):
    write = store_mod.write_account_sync
    store_mod.add_scope(
        conn,
        peer_id=-1001,
        kind="channel",
        title="News",
        username="news",
        chat_ref="@news",
    )

    # scope_channels with no cursor stored and none given leaves it NULL.
    assert write(conn, scope_channels=True) is None
    assert _account_row(conn)["changes_cursor"] is None

    # A given cursor is filtered to scoped channels before it is stored.
    given = _cursor(**{"-1001": 11, "-1002": 22})
    kept = _cursor(**{"-1001": 11})
    assert write(conn, changes_cursor=given, scope_channels=True) == kept
    assert _account_row(conn)["changes_cursor"] == kept

    # Without scope_channels the cursor is stored as given.
    assert write(conn, changes_cursor=given) == given
    assert _account_row(conn)["changes_cursor"] == given

    # scope_channels alone filters the stored cursor.
    assert write(conn, scope_channels=True) == kept
    assert _account_row(conn)["changes_cursor"] == kept


def test_write_account_sync_joins_an_open_transaction(conn, clock):
    """Inside a caller's transaction it still commits its own write."""
    conn.execute("BEGIN IMMEDIATE")
    store_mod.write_account_sync(conn, touch_sync=True)
    assert not conn.in_transaction
    assert _account_row(conn)["last_sync_at"] == _ts(1)
