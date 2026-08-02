"""Persisted governor state: cooldowns, pacing reservations, peer breadth.

SQLite under the state dir, following ADR-0060's `clone/statedb.py` pattern.
One file per machine, every row keyed by ``account_user_id`` — ADR-0072
decision 5 makes the budget account-scoped and shared across session roles, so
a second process must see the first one's clock rather than keep its own.

Three behaviours are load-bearing and each answers a specific past failure:

* **Reads fail open.** A missing or corrupt ledger must never wedge the CLI.
  ``clone/flood.py`` already works this way; the cost of failing closed on
  corruption is an account that cannot be used at all with no way out but
  hand-editing state.
* **Deadlines are clamped at read time, not at write time.** A host clock that
  ran ahead when a cooldown was armed persists a deadline no honest FloodWait
  could produce. Arming stays honest and stores the raw value; reading degrades
  it to a bounded wait.
* **``busy_timeout`` is set deliberately.** #137's inventory found none set
  anywhere today, which leaves concurrent writers to the driver default.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tgcli.session import ensure_state_dir, restrict_file, state_dir

SCHEMA_VERSION = 1

# Telegram's longest realistic FloodWait is on the order of a day; anything
# further ahead is skew or corruption (`clone/flood.py:35`, kept deliberately
# identical so the two agree while both are live).
MAX_COOLDOWN_S = 86_400

# ADR-0072 decision 3: 100 distinct peers touched by history reads per rolling
# 24 hours. A hedge against assumption 2, not a measured limit — #140's canary
# touched 15% of it and returned no signal either way.
BREADTH_WINDOW_S = 86_400
BREADTH_BUDGET = 100

# Concurrent writers park here rather than raising `database is locked`.
BUSY_TIMEOUT_MS = 5_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cooldowns (
    account_user_id INTEGER NOT NULL,
    request_key     TEXT    NOT NULL,
    deadline        TEXT    NOT NULL,
    probe_spent     INTEGER NOT NULL DEFAULT 0,
    armed_at        TEXT    NOT NULL,
    PRIMARY KEY (account_user_id, request_key)
);
CREATE TABLE IF NOT EXISTS pacing (
    account_user_id INTEGER NOT NULL,
    request_key     TEXT    NOT NULL,
    reserved_at     REAL    NOT NULL,
    PRIMARY KEY (account_user_id, request_key)
);
CREATE TABLE IF NOT EXISTS peer_touches (
    account_user_id INTEGER NOT NULL,
    peer_id         INTEGER NOT NULL,
    touched_at      REAL    NOT NULL,
    PRIMARY KEY (account_user_id, peer_id)
);
"""


def default_path() -> Path:
    return state_dir() / "governor.db"


class Ledger:
    """Governor state for one machine. Reads fail open; writes are best-effort.

    A write that cannot land is reported by returning ``False`` rather than
    raising: losing a pacing reservation degrades the pace, losing a cooldown
    arm degrades protection, and neither is worth aborting a command the user
    asked for. Callers that care can check.
    """

    def __init__(
        self, connection: sqlite3.Connection, *, degraded: bool = False
    ) -> None:
        self._db = connection
        self.degraded = degraded

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def open(cls, path: Path | None = None) -> Ledger:
        """Open the ledger, or fall back to a private in-memory one.

        Failing open is the whole contract (L3): a corrupt or unopenable
        ledger degrades protection, while failing closed would make every
        command on the account exit non-zero with no remedy but hand-deleting
        state. The fallback is a real, empty ledger — reads answer "nothing is
        cooling" and writes go nowhere — and it announces itself through
        ``degraded`` so ``doctor`` can say the governor is not persisting.
        """
        target = default_path() if path is None else path
        try:
            if target.parent == state_dir():
                ensure_state_dir()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
            connection = cls._connect(target)
        except (sqlite3.Error, OSError):
            return cls(cls._connect(None), degraded=True)
        try:
            restrict_file(target)
        except OSError:
            pass
        return cls(connection)

    @staticmethod
    def _connect(target: Path | None) -> sqlite3.Connection:
        name = ":memory:" if target is None else str(target)
        connection = sqlite3.connect(name, timeout=BUSY_TIMEOUT_MS / 1000)
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        if target is not None:
            connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(_SCHEMA)
        connection.commit()
        return connection

    def close(self) -> None:
        try:
            self._db.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- cooldowns ---------------------------------------------------------

    def cooldown_deadline(
        self, account_user_id: int, request_key: str, *, now: datetime | None = None
    ) -> datetime | None:
        """The active deadline for this request type, clamped, or ``None``."""
        moment = datetime.now(UTC) if now is None else now
        try:
            row = self._db.execute(
                "SELECT deadline FROM cooldowns "
                "WHERE account_user_id = ? AND request_key = ?",
                (account_user_id, request_key),
            ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        try:
            deadline = datetime.fromisoformat(row[0])
        except (TypeError, ValueError):
            return None
        if deadline.tzinfo is None:
            return None
        deadline = deadline.astimezone(UTC)
        clamped = min(deadline, moment + timedelta(seconds=MAX_COOLDOWN_S))
        return clamped if clamped > moment else None

    def cooldown_armed_at(
        self, account_user_id: int, request_key: str
    ) -> datetime | None:
        """When the current cooldown record was armed, or ``None``.

        Read for the probe's elapsed-fraction computation (plan phase 3);
        the deadline alone cannot say how much of the wait is already over.
        Fails open like every other read, and an unparseable value reads as
        "not yet 50%" — the probe refuses rather than sends into a guess.
        """
        try:
            row = self._db.execute(
                "SELECT armed_at FROM cooldowns "
                "WHERE account_user_id = ? AND request_key = ?",
                (account_user_id, request_key),
            ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        try:
            armed_at = datetime.fromisoformat(row[0])
        except (TypeError, ValueError):
            return None
        if armed_at.tzinfo is None:
            return None
        return armed_at.astimezone(UTC)

    def arm_cooldown(
        self,
        account_user_id: int,
        request_key: str,
        deadline: datetime,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Record a server-confirmed deadline, resetting the probe budget.

        A fresh deadline earns a fresh probe: ADR-0072 decision 1 bounds the
        probe's cost at one request *per confirmed deadline*, which is what
        makes a wrong assumption 1 cost one extra request rather than a loop.
        """
        moment = datetime.now(UTC) if now is None else now
        try:
            self._db.execute(
                "INSERT INTO cooldowns "
                "(account_user_id, request_key, deadline, probe_spent, armed_at) "
                "VALUES (?, ?, ?, 0, ?) "
                "ON CONFLICT(account_user_id, request_key) DO UPDATE SET "
                "deadline = excluded.deadline, probe_spent = 0, "
                "armed_at = excluded.armed_at",
                (
                    account_user_id,
                    request_key,
                    deadline.astimezone(UTC).isoformat(),
                    moment.isoformat(),
                ),
            )
            self._db.commit()
        except sqlite3.Error:
            return False
        return True

    def clear_cooldown(self, account_user_id: int, request_key: str) -> bool:
        try:
            self._db.execute(
                "DELETE FROM cooldowns WHERE account_user_id = ? AND request_key = ?",
                (account_user_id, request_key),
            )
            self._db.commit()
        except sqlite3.Error:
            return False
        return True

    def probe_spent(self, account_user_id: int, request_key: str) -> bool:
        try:
            row = self._db.execute(
                "SELECT probe_spent FROM cooldowns "
                "WHERE account_user_id = ? AND request_key = ?",
                (account_user_id, request_key),
            ).fetchone()
        except sqlite3.Error:
            # Fail open on reads, but a probe is a request into a live
            # penalty: treat an unreadable ledger as "already spent" so the
            # uncertain case costs nothing.
            return True
        return bool(row[0]) if row is not None else False

    def spend_probe(
        self,
        account_user_id: int,
        request_key: str,
        *,
        expected_deadline: datetime | None = None,
    ) -> bool:
        """Mark the probe spent *before* it is attempted (ADR-0072 decision 5).

        Write-ahead is the whole point: a crash between marking and sending
        must leave the record spent, so the next invocation waits the deadline
        out instead of probing again.

        ``expected_deadline`` pins the record the probe was decided against:
        a concurrent re-arm (fresh flood on the other process's own probe)
        must not be claimed at 0% of its wait — the claim lands only on the
        exact deadline the 50%-window was computed from (review fix M7).
        """
        try:
            if expected_deadline is None:
                cursor = self._db.execute(
                    "UPDATE cooldowns SET probe_spent = 1 "
                    "WHERE account_user_id = ? AND request_key = ? AND probe_spent = 0",
                    (account_user_id, request_key),
                )
            else:
                cursor = self._db.execute(
                    "UPDATE cooldowns SET probe_spent = 1 "
                    "WHERE account_user_id = ? AND request_key = ? "
                    "AND probe_spent = 0 AND deadline = ?",
                    (
                        account_user_id,
                        request_key,
                        expected_deadline.astimezone(UTC).isoformat(),
                    ),
                )
            self._db.commit()
        except sqlite3.Error:
            return False
        return cursor.rowcount == 1

    def active_cooldowns(
        self, account_user_id: int, *, now: datetime | None = None
    ) -> dict[str, datetime]:
        """Every request type currently cooling, for ``doctor`` to report."""
        moment = datetime.now(UTC) if now is None else now
        try:
            rows = self._db.execute(
                "SELECT request_key FROM cooldowns WHERE account_user_id = ?",
                (account_user_id,),
            ).fetchall()
        except sqlite3.Error:
            return {}
        active = {}
        for (request_key,) in rows:
            deadline = self.cooldown_deadline(account_user_id, request_key, now=moment)
            if deadline is not None:
                active[request_key] = deadline
        return active

    # -- pacing ------------------------------------------------------------

    def last_reserved(self, account_user_id: int, request_key: str) -> float | None:
        try:
            row = self._db.execute(
                "SELECT reserved_at FROM pacing "
                "WHERE account_user_id = ? AND request_key = ?",
                (account_user_id, request_key),
            ).fetchone()
        except sqlite3.Error:
            return None
        return float(row[0]) if row is not None else None

    def reserve(self, account_user_id: int, request_key: str, at: float) -> bool:
        """Claim the next dispatch slot for this request type.

        Called *before* the request leaves (ADR-0072 decision 3): the interval
        is start-to-start, so a slow request must not add its own latency on
        top of the pace.
        """
        try:
            self._db.execute(
                "INSERT INTO pacing (account_user_id, request_key, reserved_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(account_user_id, request_key) DO UPDATE SET "
                "reserved_at = excluded.reserved_at",
                (account_user_id, request_key, at),
            )
            self._db.commit()
        except sqlite3.Error:
            return False
        return True

    def clamp_reservation(self, account_user_id: int, request_key: str, now: float):
        """Repair a reservation stamped in the future by a stepped-back clock.

        Same failure `resolve_phone.py` already guards: without this, one NTP
        correction wedges a request type until the wall clock catches up.
        """
        last = self.last_reserved(account_user_id, request_key)
        if last is not None and last > now:
            self.reserve(account_user_id, request_key, now)
            return now
        return last

    # -- peer breadth ------------------------------------------------------

    def touch_peer(self, account_user_id: int, peer_id: int, at: float) -> bool:
        """Record that a history read touched this peer.

        Durable per peer rather than per run (ADR-0072 decision 5): a killed
        process must not hand back budget for peers it really did read.
        """
        try:
            self._db.execute(
                "INSERT INTO peer_touches (account_user_id, peer_id, touched_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(account_user_id, peer_id) DO UPDATE SET "
                "touched_at = excluded.touched_at",
                (account_user_id, peer_id, at),
            )
            self._db.commit()
        except sqlite3.Error:
            return False
        return True

    def peers_in_window(
        self, account_user_id: int, now: float, *, window: float = BREADTH_WINDOW_S
    ) -> int:
        try:
            row = self._db.execute(
                "SELECT COUNT(*) FROM peer_touches "
                "WHERE account_user_id = ? AND touched_at >= ?",
                (account_user_id, now - window),
            ).fetchone()
        except sqlite3.Error:
            return 0
        return int(row[0]) if row is not None else 0

    def breadth_remaining(
        self, account_user_id: int, now: float, *, budget: int = BREADTH_BUDGET
    ) -> int:
        return max(0, budget - self.peers_in_window(account_user_id, now))
