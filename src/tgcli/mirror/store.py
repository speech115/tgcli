import fcntl
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tgcli.errors import ConfigError


_MIN_SIGNED_64 = -(2**63)
_MAX_SIGNED_64 = 2**63 - 1


def mirror_id(account_user_id: int, source_peer_id: int) -> str:
    identity = f"{account_user_id}:{source_peer_id}".encode()
    return hashlib.sha256(identity).hexdigest()


def _state_dir() -> Path:
    return Path(os.environ.get("TGCLI_STATE_DIR", "~/.local/state/tgcli")).expanduser()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def _account_hash(account_user_id: int) -> str:
    return hashlib.sha256(str(account_user_id).encode()).hexdigest()


def _cooldown_path(account_user_id: int) -> Path:
    return (
        _state_dir()
        / "mirrors"
        / "cooldowns"
        / f"{_account_hash(account_user_id)}.json"
    )


def _open_private_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(fd, 0o600)
    return fd


@contextmanager
def account_mutation_lock(account_user_id: int):
    path = (
        _state_dir()
        / "mirrors"
        / "locks"
        / f"{_account_hash(account_user_id)}.lock"
    )
    fd = _open_private_lock(path)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigError(
                "mirror account is busy (another mutation is in progress); retry later"
            ) from None
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextmanager
def _cooldown_write_lock(account_user_id: int):
    path = (
        _state_dir()
        / "mirrors"
        / "cooldowns"
        / f"{_account_hash(account_user_id)}.lock"
    )
    fd = _open_private_lock(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_cooldown(path: Path) -> datetime | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _as_utc(datetime.fromisoformat(payload["retry_not_before"]))


def cooldown_deadline(account_user_id: int) -> datetime | None:
    return _read_cooldown(_cooldown_path(account_user_id))


def record_cooldown(
    account_user_id: int, retry_after: int, now: datetime | None = None
) -> datetime:
    if isinstance(retry_after, bool) or not isinstance(retry_after, int):
        raise TypeError("retry_after must be an integer")
    if retry_after < 0:
        raise ValueError("retry_after must not be negative")

    path = _cooldown_path(account_user_id)
    with _cooldown_write_lock(account_user_id):
        current_time = _as_utc(now or datetime.now(timezone.utc))
        deadline = current_time + timedelta(seconds=retry_after)
        existing = _read_cooldown(path)
        if existing is not None:
            deadline = max(deadline, existing)

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                os.chmod(handle.name, 0o600)
                json.dump({"retry_not_before": deadline.isoformat()}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    return deadline


@dataclass(frozen=True)
class MirrorRecord:
    mirror_id: str
    account_user_id: int
    source_peer_id: int
    source_title: str
    destination_peer_id: int | None
    authorized: bool
    creation_marker: str | None
    creation_state: str
    create_attempted_at: str | None
    retention_class: str


@dataclass(frozen=True)
class CopyOperation:
    source_peer_id: int
    source_message_id: int
    random_id: int
    destination_message_id: int | None


class MirrorStore:
    def __init__(self) -> None:
        self._path: Path | None = None

    @property
    def path(self) -> Path:
        if self._path is None:
            raise RuntimeError("mirror store has not been created")
        return self._path

    def create(
        self, account_user_id: int, source_peer_id: int, source_title: str
    ) -> MirrorRecord:
        identity = mirror_id(account_user_id, source_peer_id)
        mirrors_dir = _state_dir() / "mirrors"
        mirrors_dir.mkdir(parents=True, exist_ok=True)
        self._path = mirrors_dir / f"{identity}.db"

        with self._connect() as connection:
            self._create_schema(connection)
            connection.execute(
                """
                INSERT INTO mirrors (
                    mirror_id, account_user_id, source_peer_id, source_title
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(mirror_id) DO NOTHING
                """,
                (identity, account_user_id, source_peer_id, source_title),
            )
            connection.execute(
                """
                UPDATE mirrors
                SET source_title = ?
                WHERE mirror_id = ?
                """,
                (source_title, identity),
            )
            record = self._read_record(connection)

        if (
            record.mirror_id != identity
            or record.account_user_id != account_user_id
            or record.source_peer_id != source_peer_id
        ):
            raise RuntimeError("mirror database identity does not match its path")
        return record

    def authorize(self, destination_peer_id: int) -> MirrorRecord:
        with self._connect() as connection:
            current = self._read_record(connection)
            if (
                current.destination_peer_id is not None
                and current.destination_peer_id != destination_peer_id
            ):
                raise ValueError("mirror is already authorized for another destination")
            connection.execute(
                """
                UPDATE mirrors
                SET destination_peer_id = ?, authorized = 1,
                    creation_state = 'authorized',
                    retention_class = 'user_owned_retained'
                WHERE mirror_id = ?
                """,
                (destination_peer_id, current.mirror_id),
            )
            return self._read_record(connection)

    def mark_create_dispatched(
        self, marker: str, attempted_at: datetime
    ) -> MirrorRecord:
        with self._connect() as connection:
            current = self._read_record(connection)
            connection.execute(
                """
                UPDATE mirrors
                SET creation_marker = ?, creation_state = 'reconcile_required',
                    create_attempted_at = ?
                WHERE mirror_id = ?
                """,
                (marker, _as_utc(attempted_at).isoformat(), current.mirror_id),
            )
            return self._read_record(connection)

    def mark_create_blocked(self) -> MirrorRecord:
        with self._connect() as connection:
            current = self._read_record(connection)
            connection.execute(
                """
                UPDATE mirrors
                SET creation_state = 'blocked'
                WHERE mirror_id = ?
                """,
                (current.mirror_id,),
            )
            return self._read_record(connection)

    def prepare_copy(
        self, source_message_id: int, random_id: int | None = None
    ) -> CopyOperation:
        if random_id is None:
            random_id = self._random_id()
        self._validate_random_id(random_id)

        with self._connect() as connection:
            record = self._read_record(connection)
            connection.execute(
                """
                INSERT INTO copy_operations (
                    source_peer_id, source_message_id, random_id
                ) VALUES (?, ?, ?)
                ON CONFLICT(source_peer_id, source_message_id) DO NOTHING
                """,
                (record.source_peer_id, source_message_id, random_id),
            )
            return self._read_operation(connection, record.source_peer_id, source_message_id)

    def confirm_copy(
        self, source_message_id: int, destination_message_id: int
    ) -> CopyOperation:
        with self._connect() as connection:
            record = self._read_record(connection)
            operation = self._read_operation(
                connection, record.source_peer_id, source_message_id
            )
            if (
                operation.destination_message_id is not None
                and operation.destination_message_id != destination_message_id
            ):
                raise ValueError("copy is already confirmed with another destination message")
            connection.execute(
                """
                UPDATE copy_operations
                SET destination_message_id = ?
                WHERE source_peer_id = ? AND source_message_id = ?
                """,
                (destination_message_id, record.source_peer_id, source_message_id),
            )
            connection.execute(
                """
                UPDATE mirrors
                SET high_water_message_id = max(high_water_message_id, ?)
                WHERE mirror_id = ?
                """,
                (source_message_id, record.mirror_id),
            )
            return self._read_operation(
                connection, record.source_peer_id, source_message_id
            )

    def pending_copies(self) -> list[CopyOperation]:
        with self._connect() as connection:
            source_peer_id = self._read_record(connection).source_peer_id
            rows = connection.execute(
                """
                SELECT source_peer_id, source_message_id, random_id,
                       destination_message_id
                FROM copy_operations
                WHERE source_peer_id = ? AND destination_message_id IS NULL
                ORDER BY source_message_id
                """,
                (source_peer_id,),
            ).fetchall()
        return [CopyOperation(*row) for row in rows]

    def last_confirmed_message_id(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT high_water_message_id FROM mirrors"
            ).fetchone()
        if row is None:
            raise RuntimeError("mirror database has no metadata")
        return int(row[0])

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS mirrors (
                mirror_id TEXT PRIMARY KEY,
                account_user_id INTEGER NOT NULL,
                source_peer_id INTEGER NOT NULL,
                source_title TEXT NOT NULL,
                destination_peer_id INTEGER,
                authorized INTEGER NOT NULL DEFAULT 0 CHECK (authorized IN (0, 1)),
                creation_marker TEXT,
                creation_state TEXT NOT NULL DEFAULT 'planned'
                    CHECK (creation_state IN (
                        'planned', 'reconcile_required', 'blocked', 'authorized'
                    )),
                create_attempted_at TEXT,
                retention_class TEXT NOT NULL DEFAULT 'provisional'
                    CHECK (retention_class IN (
                        'provisional', 'user_owned_retained'
                    )),
                high_water_message_id INTEGER NOT NULL DEFAULT 0
                    CHECK (high_water_message_id >= 0),
                UNIQUE (account_user_id, source_peer_id),
                CHECK (
                    (authorized = 0 AND destination_peer_id IS NULL)
                    OR (authorized = 1 AND destination_peer_id IS NOT NULL)
                )
            );

            CREATE TABLE IF NOT EXISTS copy_operations (
                source_peer_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL CHECK (source_message_id > 0),
                random_id INTEGER NOT NULL,
                destination_message_id INTEGER CHECK (destination_message_id > 0),
                PRIMARY KEY (source_peer_id, source_message_id)
            );
            """
        )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(mirrors)")
        }
        if "creation_marker" not in columns:
            connection.execute("ALTER TABLE mirrors ADD COLUMN creation_marker TEXT")
        if "creation_state" not in columns:
            connection.execute(
                """
                ALTER TABLE mirrors ADD COLUMN creation_state TEXT NOT NULL
                DEFAULT 'planned' CHECK (creation_state IN (
                    'planned', 'reconcile_required', 'blocked', 'authorized'
                ))
                """
            )
        if "create_attempted_at" not in columns:
            connection.execute("ALTER TABLE mirrors ADD COLUMN create_attempted_at TEXT")
        if "retention_class" not in columns:
            connection.execute(
                """
                ALTER TABLE mirrors ADD COLUMN retention_class TEXT NOT NULL
                DEFAULT 'provisional' CHECK (retention_class IN (
                    'provisional', 'user_owned_retained'
                ))
                """
            )
        connection.execute(
            """
            UPDATE mirrors
            SET creation_state = 'authorized',
                retention_class = 'user_owned_retained'
            WHERE authorized = 1
            """
        )

    @staticmethod
    def _read_record(connection: sqlite3.Connection) -> MirrorRecord:
        row = connection.execute(
            """
            SELECT mirror_id, account_user_id, source_peer_id, source_title,
                   destination_peer_id, authorized, creation_marker,
                   creation_state, create_attempted_at, retention_class
            FROM mirrors
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("mirror database has no metadata")
        return MirrorRecord(*row[:5], bool(row[5]), *row[6:])

    @staticmethod
    def _read_operation(
        connection: sqlite3.Connection, source_peer_id: int, source_message_id: int
    ) -> CopyOperation:
        row = connection.execute(
            """
            SELECT source_peer_id, source_message_id, random_id,
                   destination_message_id
            FROM copy_operations
            WHERE source_peer_id = ? AND source_message_id = ?
            """,
            (source_peer_id, source_message_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"copy operation is not prepared: {source_message_id}")
        return CopyOperation(*row)

    @staticmethod
    def _random_id() -> int:
        value = secrets.randbits(64)
        return value if value <= _MAX_SIGNED_64 else value - 2**64

    @staticmethod
    def _validate_random_id(random_id: int) -> None:
        if isinstance(random_id, bool) or not isinstance(random_id, int):
            raise TypeError("random_id must be an integer")
        if not _MIN_SIGNED_64 <= random_id <= _MAX_SIGNED_64:
            raise ValueError("random_id must fit in a signed 64-bit integer")
