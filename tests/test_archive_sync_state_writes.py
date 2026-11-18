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


def _account_row(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM account_sync WHERE id = 1").fetchone()
    return None if row is None else dict(row)


def _cursor() -> str:
    return cursor_codec.encode(ChangesCursor(pts=5, qts=1, date=1_735_689_600, seq=1))


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
            gaps=[{"reason": "différenceTooLong", "pts": 50}],
            touch_sync=True,
            pending_catchups=[-1003, -1001],
        )
        == first
    )
    assert _account_row(conn) == {
        "id": 1,
        "changes_cursor": first,
        "gap_json": '[{"reason":"différenceTooLong","pts":50}]',
        "last_sync_at": _ts(2),
        "last_reconcile_at": None,
        "reconcile_json": None,
        "private_enum_json": None,
        "pending_catchups_json": "[-1003, -1001]",
    }

    after_reconcile = _account_row(conn)

    # An empty pending list is stored as NULL; a later gap joins the old one.
    write(conn, pending_catchups=[], gaps=[{"reason": "channel"}])
    assert _account_row(conn) == {
        **after_reconcile,
        "gap_json": '[{"reason":"différenceTooLong","pts":50},{"reason":"channel"}]',
        "pending_catchups_json": None,
    }

    # clear_gap wins over a gap passed in the same call.
    write(conn, gaps=[{"reason": "ignored"}], clear_gap=True, touch_sync=True)
    assert _account_row(conn) == {
        **after_reconcile,
        "gap_json": None,
        "last_sync_at": _ts(6),
        "pending_catchups_json": None,
    }


def test_write_account_sync_joins_an_open_transaction(conn, clock):
    """Inside a caller's transaction it still commits its own write."""
    conn.execute("BEGIN IMMEDIATE")
    store_mod.write_account_sync(conn, touch_sync=True)
    assert not conn.in_transaction
    assert _account_row(conn)["last_sync_at"] == _ts(1)


async def test_a_catchup_flood_keeps_a_finished_backfill_finished(conn):
    """`more` says older history remains, and only
    backfill decides it. A catch-up that flooded mid-page wrote `more: true`
    onto a channel whose backfill had reached the start, so `status` reported
    history that is not there and `--private` style skipping stopped."""
    from telethon import errors as telethon_errors
    from telethon.tl.types import Channel, PeerChannel

    from tests.fakes.archive import archive_msg
    from tgcli.archive import sync as sync_mod
    from tgcli.errors import RateLimitError

    channel = Channel(
        id=1001, title="News", photo=None, date=None, access_hash=1, broadcast=True
    )
    peer = -1001000001001
    with conn:
        store_mod.upsert_sync_state(
            conn, peer, oldest_id=1, newest_id=50, more=False, last_error=None
        )

    class Tg:
        async def get_entity(self, ref):
            return channel

        async def iter_messages(self, entity, **kwargs):
            yield archive_msg(mid=51, peer=PeerChannel(1001))
            raise telethon_errors.FloodWaitError(request=None, capture=30)

    with pytest.raises(RateLimitError):
        await sync_mod._catch_up_peer(
            Tg(), conn, peer, account_user_id=42, message_budget=50
        )

    row = store_mod.get_sync_state(conn, peer)
    assert row["newest_id"] == 51
    assert row["last_error"] == "FLOOD_WAIT:30"
    assert row["more"] is False
