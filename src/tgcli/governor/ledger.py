"""Persisted flood cooldowns, shared by every tgcli process on this machine.

SQLite under the state dir. One row per (account, request type) that drew a
FloodWait, holding the server's own deadline, so the next process refuses
that type locally instead of sending into a live penalty.

* **A missing or corrupt file degrades, never crashes.** ``open`` returns a
  private in-memory ledger with ``degraded=True``; ``doctor`` reports it, and
  traffic proceeds without cross-process memory.
* **A failed durable arm stays sticky in-process** (ADR-0090):
  ``remember_cooldown`` keeps the deadline so the next same-type RPC in this
  process still refuses.
* **Deadlines are clamped at read time**, so a deadline armed under a skewed
  clock degrades to a bounded wait instead of a permanent refusal.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tgcli.session import ensure_state_dir, restrict_file, state_dir

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
        self, connection: sqlite3.Connection, *, degraded: bool = False
    ) -> None:
        self._db = connection
        self.degraded = degraded
        # Process-local deadlines for arms that could not be written (ADR-0090).
        self._volatile_cooldowns: dict[tuple[int, str], datetime] = {}

    @classmethod
    def open(cls, path: Path | None = None) -> Ledger:
        """Open the ledger, or fall back to a private in-memory one."""
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

    def remember_cooldown(
        self, account_user_id: int, request_key: str, deadline: datetime
    ) -> None:
        """Keep a process-local cooldown when the durable arm cannot land."""
        self._volatile_cooldowns[(account_user_id, request_key)] = deadline.astimezone(
            UTC
        )

    def cooldown_deadline(
        self, account_user_id: int, request_key: str, *, now: datetime | None = None
    ) -> datetime | None:
        """The active deadline for this request type, clamped, or ``None``."""
        moment = datetime.now(UTC) if now is None else now
        deadline: datetime | None = None
        try:
            row = self._db.execute(
                "SELECT deadline FROM cooldowns "
                "WHERE account_user_id = ? AND request_key = ?",
                (account_user_id, request_key),
            ).fetchone()
        except sqlite3.Error:
            row = None
        if row is not None:
            try:
                parsed = datetime.fromisoformat(row[0])
            except (TypeError, ValueError):
                parsed = None
            if parsed is not None and parsed.tzinfo is not None:
                deadline = parsed.astimezone(UTC)
        volatile = self._volatile_cooldowns.get((account_user_id, request_key))
        if volatile is not None:
            deadline = volatile if deadline is None else max(deadline, volatile)
        if deadline is None:
            return None
        clamped = min(deadline, moment + timedelta(seconds=MAX_COOLDOWN_S))
        return clamped if clamped > moment else None

    def arm_cooldown(
        self,
        account_user_id: int,
        request_key: str,
        deadline: datetime,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Record a server-confirmed deadline; ``False`` when it cannot land."""
        moment = datetime.now(UTC) if now is None else now
        aware = deadline.astimezone(UTC)
        try:
            self._db.execute(
                "INSERT INTO cooldowns "
                "(account_user_id, request_key, deadline, armed_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(account_user_id, request_key) DO UPDATE SET "
                "deadline = excluded.deadline, armed_at = excluded.armed_at",
                (account_user_id, request_key, aware.isoformat(), moment.isoformat()),
            )
            self._db.commit()
        except sqlite3.Error:
            self.remember_cooldown(account_user_id, request_key, aware)
            return False
        self._volatile_cooldowns.pop((account_user_id, request_key), None)
        return True

    def active_cooldowns(
        self, account_user_id: int, *, now: datetime | None = None
    ) -> dict[str, datetime]:
        """Every request type currently cooling, for ``doctor`` to report."""
        moment = datetime.now(UTC) if now is None else now
        keys: set[str] = set()
        try:
            rows = self._db.execute(
                "SELECT request_key FROM cooldowns WHERE account_user_id = ?",
                (account_user_id,),
            ).fetchall()
        except sqlite3.Error:
            rows = ()
        keys.update(request_key for (request_key,) in rows)
        keys.update(
            key
            for (account, key), deadline in self._volatile_cooldowns.items()
            if account == account_user_id and deadline > moment
        )
        active = {}
        for request_key in keys:
            deadline = self.cooldown_deadline(account_user_id, request_key, now=moment)
            if deadline is not None:
                active[request_key] = deadline
        return active
