import hashlib
import os
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path


_MIN_SIGNED_64 = -(2**63)
_MAX_SIGNED_64 = 2**63 - 1


def mirror_id(account_user_id: int, source_peer_id: int) -> str:
    identity = f"{account_user_id}:{source_peer_id}".encode()
    return hashlib.sha256(identity).hexdigest()


def _state_dir() -> Path:
    return Path(os.environ.get("TGCLI_STATE_DIR", "~/.local/state/tgcli")).expanduser()


@dataclass(frozen=True)
class MirrorRecord:
    mirror_id: str
    account_user_id: int
    source_peer_id: int
    source_title: str
    destination_peer_id: int | None
    authorized: bool


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
                SET destination_peer_id = ?, authorized = 1
                WHERE mirror_id = ?
                """,
                (destination_peer_id, current.mirror_id),
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

    @staticmethod
    def _read_record(connection: sqlite3.Connection) -> MirrorRecord:
        row = connection.execute(
            """
            SELECT mirror_id, account_user_id, source_peer_id, source_title,
                   destination_peer_id, authorized
            FROM mirrors
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("mirror database has no metadata")
        return MirrorRecord(*row[:-1], authorized=bool(row[-1]))

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
