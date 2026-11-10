"""Persisted flood cooldowns, shared by every tgcli process on this machine.

SQLite under the state dir. One row per (account, request type) that drew a
FloodWait, holding the server's own deadline, so the next process refuses
that type locally instead of sending into a live penalty.

* **A missing or corrupt file degrades, never crashes.** ``open`` returns a
  private in-memory ledger with ``degraded=True`` and the error kept on
  ``degraded_reason``; ``doctor`` reports it, a governed client notes it on
  stderr, and traffic proceeds without cross-process memory.
* **A row ends at ``min(deadline, armed_at + MAX_COOLDOWN_S)``**, and every
  reader uses that end, so no record refuses for more than a day after it
  was armed, however far ahead its deadline says.
* **The later end wins.** The key has no peer, so a short FloodWait one
  process draws on chat B must not shorten a long penalty another process
  recorded on chat A: arming reads the stored end and writes inside one
  ``BEGIN IMMEDIATE`` transaction, and keeps whichever row ends later.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tgcli.state_paths import create_private, ensure_state_dir, restrict_file, state_dir

# Telegram's longest realistic FloodWait is on the order of a day; anything
# further ahead is skew or corruption.
MAX_COOLDOWN_S = 86_400

# Concurrent writers park here rather than raising `database is locked`.
BUSY_TIMEOUT_MS = 5_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cooldowns (
    account_user_id INTEGER NOT NULL,
    request_key     TEXT    NOT NULL,
    deadline        TEXT    NOT NULL,
    armed_at        TEXT    NOT NULL,
    PRIMARY KEY (account_user_id, request_key)
);
"""


def default_path() -> Path:
    return state_dir() / "governor.db"


class Ledger:
    """Flood cooldowns for one machine; see the module docstring."""

    def __init__(
        self, connection: sqlite3.Connection, *, degraded_reason: str | None = None
    ) -> None:
        self._db = connection
        self.degraded_reason = degraded_reason
        self.degraded = degraded_reason is not None

    @classmethod
    def open(cls, path: Path | None = None) -> Ledger:
        """Open the ledger, or fall back to a private in-memory one."""
        target = default_path() if path is None else path
        try:
            if target.parent == state_dir():
                ensure_state_dir()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
            create_private(target)
            connection = cls._connect(target)
        except (sqlite3.Error, OSError) as exc:
            reason = f"{target}: {exc}"
            return cls(cls._connect(None), degraded_reason=reason)
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

    def _end(self, account_user_id: int, request_key: str) -> datetime | None:
        """When this row's cooldown ends: its deadline, capped at
        ``MAX_COOLDOWN_S`` past its arming. ``None`` when there is no
        readable row."""
        try:
            row = self._db.execute(
                "SELECT deadline, armed_at FROM cooldowns "
                "WHERE account_user_id = ? AND request_key = ?",
                (account_user_id, request_key),
            ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        try:
            deadline = datetime.fromisoformat(row[0])
            armed = datetime.fromisoformat(row[1])
        except (TypeError, ValueError):
            return None
        if deadline.tzinfo is None or armed.tzinfo is None:
            return None
        return min(deadline, armed + timedelta(seconds=MAX_COOLDOWN_S)).astimezone(UTC)

    def cooldown_deadline(
        self, account_user_id: int, request_key: str, *, now: datetime | None = None
    ) -> datetime | None:
        """The active deadline for this request type, clamped, or ``None``.

        Clamped twice: at ``MAX_COOLDOWN_S`` past arming (the row's end) and
        at ``MAX_COOLDOWN_S`` from now, for a row armed under a clock that has
        since stepped back.
        """
        moment = datetime.now(UTC) if now is None else now
        end = self._end(account_user_id, request_key)
        if end is None:
            return None
        clamped = min(end, moment + timedelta(seconds=MAX_COOLDOWN_S))
        return clamped if clamped > moment else None

    def arm_cooldown(
        self,
        account_user_id: int,
        request_key: str,
        deadline: datetime,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Record a server-confirmed deadline; ``False`` when it cannot land.

        A stored row that ends later than this one stays: the read and the
        write share one ``BEGIN IMMEDIATE`` transaction, so two processes
        arming at once cannot both read the old row.
        """
        moment = datetime.now(UTC) if now is None else now
        aware = deadline.astimezone(UTC)
        end = min(aware, moment + timedelta(seconds=MAX_COOLDOWN_S))
        try:
            if not self._db.in_transaction:
                self._db.execute("BEGIN IMMEDIATE")
            stored = self._end(account_user_id, request_key)
            if stored is None or stored <= end:
                self._db.execute(
                    "INSERT INTO cooldowns "
                    "(account_user_id, request_key, deadline, armed_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(account_user_id, request_key) DO UPDATE SET "
                    "deadline = excluded.deadline, armed_at = excluded.armed_at",
                    (
                        account_user_id,
                        request_key,
                        aware.isoformat(),
                        moment.isoformat(),
                    ),
                )
            self._db.commit()
        except sqlite3.Error:
            try:
                self._db.rollback()
            except sqlite3.Error:
                pass
            return False
        return True

    def cooled_since(
        self, account_user_id: int, request_key: str, since: datetime
    ) -> bool:
        """Whether this type's last flood cooldown ended at or after ``since``.

        A cooldown still running counts too. The end is the row's capped
        end, the same one ``cooldown_deadline`` reads.
        """
        end = self._end(account_user_id, request_key)
        return end is not None and end >= since

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
            rows = ()
        active = {}
        for (request_key,) in rows:
            deadline = self.cooldown_deadline(account_user_id, request_key, now=moment)
            if deadline is not None:
                active[request_key] = deadline
        return active
