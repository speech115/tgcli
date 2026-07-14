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
    batch_key: str
    batch_index: int


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
        self._validate_source_message_ids([source_message_id])
        if random_id is not None:
            self._validate_random_id(random_id)
        with self._connect() as connection:
            source_peer_id = self._read_record(connection).source_peer_id
            row = connection.execute(
                """
                SELECT source_peer_id, source_message_id, random_id,
                       destination_message_id, batch_key, batch_index
                FROM copy_operations
                WHERE source_peer_id = ? AND source_message_id = ?
                """,
                (source_peer_id, source_message_id),
            ).fetchone()
        if row is not None:
            return CopyOperation(*row)
        random_ids = None if random_id is None else [random_id]
        return self.prepare_batch(
            [source_message_id],
            batch_key=f"single:{source_message_id}",
            random_ids=random_ids,
        )[0]

    def prepare_batch(
        self,
        source_message_ids: list[int],
        *,
        batch_key: str,
        random_ids: list[int] | None = None,
    ) -> list[CopyOperation]:
        source_ids = list(source_message_ids)
        self._validate_source_message_ids(source_ids)
        if not isinstance(batch_key, str):
            raise TypeError("batch_key must be a string")
        if not batch_key:
            raise ValueError("batch_key must not be empty")
        if random_ids is not None:
            random_ids = list(random_ids)
            if len(random_ids) != len(source_ids):
                raise ValueError("random_ids length must match source message ids")
            for random_id in random_ids:
                self._validate_random_id(random_id)
            if len(set(random_ids)) != len(random_ids):
                raise ValueError("random ids must be distinct")

        with self._connect() as connection:
            record = self._read_record(connection)
            existing_batch = self._read_batch(
                connection, record.source_peer_id, batch_key
            )
            if existing_batch:
                if [item.source_message_id for item in existing_batch] != source_ids:
                    raise ValueError("batch membership/order conflicts with prepared batch")
                return existing_batch

            placeholders = ", ".join("?" for _ in source_ids)
            existing_source = connection.execute(
                f"""
                SELECT source_message_id
                FROM copy_operations
                WHERE source_peer_id = ?
                  AND source_message_id IN ({placeholders})
                LIMIT 1
                """,
                (record.source_peer_id, *source_ids),
            ).fetchone()
            if existing_source is not None:
                raise ValueError("source message belongs to another batch")

            if random_ids is None:
                random_ids = []
                while len(random_ids) < len(source_ids):
                    candidate = self._random_id()
                    if candidate not in random_ids:
                        random_ids.append(candidate)

            for batch_index, (source_message_id, random_id) in enumerate(
                zip(source_ids, random_ids, strict=True)
            ):
                connection.execute(
                    """
                    INSERT INTO copy_operations (
                        source_peer_id, source_message_id, random_id,
                        batch_key, batch_index
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.source_peer_id,
                        source_message_id,
                        random_id,
                        batch_key,
                        batch_index,
                    ),
                )
            return self._read_batch(connection, record.source_peer_id, batch_key)

    def confirm_copy(
        self, source_message_id: int, destination_message_id: int
    ) -> CopyOperation:
        return self.confirm_batch({source_message_id: destination_message_id})[0]

    def confirm_batch(
        self, destination_message_ids: dict[int, int]
    ) -> list[CopyOperation]:
        mapping = dict(destination_message_ids)
        if not mapping:
            raise ValueError("confirmation requires an exact complete batch mapping")
        self._validate_source_message_ids(list(mapping))
        for destination_message_id in mapping.values():
            self._validate_destination_message_id(destination_message_id)
        if len(set(mapping.values())) != len(mapping):
            raise ValueError("destination message ids must be distinct")

        with self._connect() as connection:
            record = self._read_record(connection)
            placeholders = ", ".join("?" for _ in mapping)
            rows = connection.execute(
                f"""
                SELECT DISTINCT batch_key
                FROM copy_operations
                WHERE source_peer_id = ?
                  AND source_message_id IN ({placeholders})
                """,
                (record.source_peer_id, *mapping),
            ).fetchall()
            if len(rows) != 1:
                raise ValueError("confirmation must reference one prepared batch")
            batch = self._read_batch(connection, record.source_peer_id, rows[0][0])
            if {item.source_message_id for item in batch} != set(mapping):
                raise ValueError("confirmation requires an exact complete batch mapping")

            confirmation_states = {
                item.destination_message_id is None for item in batch
            }
            if len(confirmation_states) != 1:
                raise ValueError("batch has mixed confirmed/pending state")
            if False in confirmation_states:
                if any(
                    item.destination_message_id != mapping[item.source_message_id]
                    for item in batch
                ):
                    raise ValueError(
                        "copy is already confirmed with another destination message"
                    )
                return batch

            destination_placeholders = ", ".join("?" for _ in mapping.values())
            owned_destination = connection.execute(
                f"""
                SELECT 1
                FROM copy_operations
                WHERE source_peer_id = ?
                  AND destination_message_id IN ({destination_placeholders})
                  AND source_message_id NOT IN ({placeholders})
                LIMIT 1
                """,
                (
                    record.source_peer_id,
                    *mapping.values(),
                    *mapping,
                ),
            ).fetchone()
            if owned_destination is not None:
                raise ValueError("destination message is already owned by another copy")

            for item in batch:
                connection.execute(
                    """
                    UPDATE copy_operations
                    SET destination_message_id = ?
                    WHERE source_peer_id = ? AND source_message_id = ?
                    """,
                    (
                        mapping[item.source_message_id],
                        record.source_peer_id,
                        item.source_message_id,
                    ),
                )
            connection.execute(
                """
                UPDATE mirrors
                SET high_water_message_id = max(high_water_message_id, ?)
                WHERE mirror_id = ?
                """,
                (max(mapping), record.mirror_id),
            )
            return self._read_batch(
                connection, record.source_peer_id, batch[0].batch_key
            )

    def pending_copies(self) -> list[CopyOperation]:
        return sorted(
            (item for batch in self.pending_batches() for item in batch),
            key=lambda item: item.source_message_id,
        )

    def pending_batches(self) -> list[list[CopyOperation]]:
        with self._connect() as connection:
            source_peer_id = self._read_record(connection).source_peer_id
            rows = connection.execute(
                """
                SELECT source_peer_id, source_message_id, random_id,
                       destination_message_id, batch_key, batch_index
                FROM copy_operations
                WHERE source_peer_id = ?
                ORDER BY batch_key, batch_index
                """,
                (source_peer_id,),
            ).fetchall()

        batches: dict[str, list[CopyOperation]] = {}
        for row in rows:
            operation = CopyOperation(*row)
            batches.setdefault(operation.batch_key, []).append(operation)

        pending: list[list[CopyOperation]] = []
        for batch in batches.values():
            if [item.batch_index for item in batch] != list(range(len(batch))):
                raise ValueError("batch membership/order is not contiguous")
            states = {item.destination_message_id is None for item in batch}
            if len(states) != 1:
                raise ValueError("mixed confirmed/pending batch")
            if True in states:
                pending.append(batch)
        return sorted(
            pending,
            key=lambda batch: min(item.source_message_id for item in batch),
        )

    def destination_message_id(self, source_message_id: int) -> int | None:
        self._validate_source_message_ids([source_message_id])
        with self._connect() as connection:
            source_peer_id = self._read_record(connection).source_peer_id
            row = connection.execute(
                """
                SELECT destination_message_id
                FROM copy_operations
                WHERE source_peer_id = ? AND source_message_id = ?
                  AND destination_message_id IS NOT NULL
                """,
                (source_peer_id, source_message_id),
            ).fetchone()
        return None if row is None else int(row[0])

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
        MirrorStore._preflight_copy_operation_tables(connection)
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
                source_message_id INTEGER NOT NULL
                    CHECK (typeof(source_message_id) = 'integer' AND source_message_id > 0),
                random_id INTEGER NOT NULL CHECK (typeof(random_id) = 'integer'),
                destination_message_id INTEGER
                    CHECK (
                        destination_message_id IS NULL OR (
                            typeof(destination_message_id) = 'integer'
                            AND destination_message_id > 0
                        )
                    ),
                batch_key TEXT NOT NULL
                    CHECK (typeof(batch_key) = 'text' AND length(batch_key) > 0),
                batch_index INTEGER NOT NULL
                    CHECK (typeof(batch_index) = 'integer' AND batch_index >= 0),
                PRIMARY KEY (source_peer_id, source_message_id),
                UNIQUE (source_peer_id, batch_key, batch_index),
                UNIQUE (source_peer_id, random_id),
                UNIQUE (source_peer_id, destination_message_id)
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
        MirrorStore._migrate_copy_operations(connection)

    @staticmethod
    def _preflight_copy_operation_tables(connection: sqlite3.Connection) -> None:
        known_names = {
            "copy_operations",
            "copy_operations_legacy",
            "copy_operations_rebuild",
        }
        present = {
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name IN (?, ?, ?)
                """,
                tuple(sorted(known_names)),
            )
        }
        if "copy_operations" in present:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(copy_operations)")
            }
            base_columns = {
                "source_peer_id",
                "source_message_id",
                "random_id",
                "destination_message_id",
            }
            allowed_columns = {
                frozenset(base_columns),
                frozenset(base_columns | {"batch_key"}),
                frozenset(base_columns | {"batch_index"}),
                frozenset(base_columns | {"batch_key", "batch_index"}),
            }
            if frozenset(columns) not in allowed_columns:
                raise ValueError(
                    "unsupported copy_operations schema; explicit migration required"
                )

            has_batch_key = "batch_key" in columns
            has_batch_index = "batch_index" in columns
            if has_batch_key != has_batch_index:
                cannot_reconstruct = False
                if has_batch_key:
                    cannot_reconstruct = connection.execute(
                        """
                        SELECT 1 FROM copy_operations
                        GROUP BY source_peer_id, batch_key
                        HAVING COUNT(*) > 1
                        LIMIT 1
                        """
                    ).fetchone() is not None
                else:
                    cannot_reconstruct = connection.execute(
                        """
                        SELECT 1 FROM copy_operations
                        WHERE batch_index IS NULL OR batch_index != 0
                        LIMIT 1
                        """
                    ).fetchone() is not None
                if cannot_reconstruct:
                    raise ValueError(
                        "cannot reconstruct partial batch metadata without ambiguity"
                    )

        artifacts = present - {"copy_operations"}
        nonempty_artifacts = [
            name
            for name in sorted(artifacts)
            if connection.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone()
            is not None
        ]
        if nonempty_artifacts:
            raise ValueError(
                "copy operation recovery artifact contains data; manual recovery required"
            )
        if artifacts:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for name in sorted(artifacts):
                    connection.execute(f"DROP TABLE {name}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _migrate_copy_operations(connection: sqlite3.Connection) -> None:
        if MirrorStore._copy_schema_is_strict(connection):
            return

        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(copy_operations)")
        }
        has_batch_key = "batch_key" in columns
        has_batch_index = "batch_index" in columns
        batch_key = "batch_key" if has_batch_key else "'single:' || source_message_id"
        batch_index = "batch_index" if has_batch_index else "CAST(0 AS INTEGER)"

        if has_batch_key and connection.execute(
            "SELECT 1 FROM copy_operations WHERE batch_key IS NULL LIMIT 1"
        ).fetchone():
            raise ValueError("batch metadata must not be NULL")
        if has_batch_index and connection.execute(
            "SELECT 1 FROM copy_operations WHERE batch_index IS NULL LIMIT 1"
        ).fetchone():
            raise ValueError("batch metadata must not be NULL")
        if connection.execute(
            f"""
            SELECT 1 FROM copy_operations
            WHERE typeof(source_peer_id) != 'integer'
               OR typeof(source_message_id) != 'integer' OR source_message_id <= 0
               OR typeof(random_id) != 'integer'
               OR (
                    destination_message_id IS NOT NULL AND (
                        typeof(destination_message_id) != 'integer'
                        OR destination_message_id <= 0
                    )
               )
               OR typeof({batch_key}) != 'text' OR length({batch_key}) = 0
               OR typeof({batch_index}) != 'integer' OR {batch_index} < 0
            LIMIT 1
            """
        ).fetchone():
            raise ValueError("copy operation violates strict schema")
        if connection.execute(
            f"""
            SELECT 1 FROM copy_operations
            GROUP BY source_peer_id, {batch_key}, {batch_index}
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone():
            raise ValueError("duplicate batch membership")
        if connection.execute(
            """
            SELECT 1 FROM copy_operations
            GROUP BY source_peer_id, random_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone():
            raise ValueError("duplicate random id ownership")
        if connection.execute(
            """
            SELECT 1 FROM copy_operations
            WHERE destination_message_id IS NOT NULL
            GROUP BY source_peer_id, destination_message_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone():
            raise ValueError("duplicate destination message ownership")

        source_count = connection.execute(
            "SELECT COUNT(*) FROM copy_operations"
        ).fetchone()[0]
        connection.commit()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE copy_operations_rebuild (
                    source_peer_id INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL
                        CHECK (
                            typeof(source_message_id) = 'integer'
                            AND source_message_id > 0
                        ),
                    random_id INTEGER NOT NULL CHECK (typeof(random_id) = 'integer'),
                    destination_message_id INTEGER
                        CHECK (
                            destination_message_id IS NULL OR (
                                typeof(destination_message_id) = 'integer'
                                AND destination_message_id > 0
                            )
                        ),
                    batch_key TEXT NOT NULL
                        CHECK (
                            typeof(batch_key) = 'text' AND length(batch_key) > 0
                        ),
                    batch_index INTEGER NOT NULL
                        CHECK (
                            typeof(batch_index) = 'integer' AND batch_index >= 0
                        ),
                    PRIMARY KEY (source_peer_id, source_message_id),
                    UNIQUE (source_peer_id, batch_key, batch_index),
                    UNIQUE (source_peer_id, random_id),
                    UNIQUE (source_peer_id, destination_message_id)
                )
                """
            )
            connection.execute(
                f"""
                INSERT INTO copy_operations_rebuild (
                    source_peer_id, source_message_id, random_id,
                    destination_message_id, batch_key, batch_index
                )
                SELECT source_peer_id, source_message_id, random_id,
                       destination_message_id, {batch_key}, {batch_index}
                FROM copy_operations
                """
            )
            target_count = connection.execute(
                "SELECT COUNT(*) FROM copy_operations_rebuild"
            ).fetchone()[0]
            if target_count != source_count:
                raise RuntimeError("copy operation migration row count mismatch")
            connection.execute("DROP TABLE copy_operations")
            connection.execute(
                "ALTER TABLE copy_operations_rebuild RENAME TO copy_operations"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _copy_schema_is_strict(connection: sqlite3.Connection) -> bool:
        rows = connection.execute("PRAGMA table_info(copy_operations)").fetchall()
        columns = {row[1]: row for row in rows}
        if set(columns) != {
            "source_peer_id",
            "source_message_id",
            "random_id",
            "destination_message_id",
            "batch_key",
            "batch_index",
        }:
            return False
        if not all(
            columns[name][3]
            for name in (
                "source_peer_id",
                "source_message_id",
                "random_id",
                "batch_key",
                "batch_index",
            )
        ):
            return False
        unique_columns = {
            tuple(
                row[2]
                for row in connection.execute(f"PRAGMA index_info('{index[1]}')")
            )
            for index in connection.execute("PRAGMA index_list(copy_operations)")
            if index[2]
        }
        required_unique = {
            ("source_peer_id", "source_message_id"),
            ("source_peer_id", "batch_key", "batch_index"),
            ("source_peer_id", "random_id"),
            ("source_peer_id", "destination_message_id"),
        }
        if not required_unique.issubset(unique_columns):
            return False
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'copy_operations'"
        ).fetchone()[0]
        return all(
            token in schema
            for token in (
                "typeof(source_message_id)",
                "typeof(random_id)",
                "typeof(destination_message_id)",
                "typeof(batch_key)",
                "typeof(batch_index)",
            )
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
                   destination_message_id, batch_key, batch_index
            FROM copy_operations
            WHERE source_peer_id = ? AND source_message_id = ?
            """,
            (source_peer_id, source_message_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"copy operation is not prepared: {source_message_id}")
        return CopyOperation(*row)

    @staticmethod
    def _read_batch(
        connection: sqlite3.Connection, source_peer_id: int, batch_key: str
    ) -> list[CopyOperation]:
        rows = connection.execute(
            """
            SELECT source_peer_id, source_message_id, random_id,
                   destination_message_id, batch_key, batch_index
            FROM copy_operations
            WHERE source_peer_id = ? AND batch_key = ?
            ORDER BY batch_index
            """,
            (source_peer_id, batch_key),
        ).fetchall()
        return [CopyOperation(*row) for row in rows]

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

    @staticmethod
    def _validate_source_message_ids(source_message_ids: list[int]) -> None:
        if not source_message_ids:
            raise ValueError("source message ids must not be empty")
        for source_message_id in source_message_ids:
            if isinstance(source_message_id, bool) or not isinstance(
                source_message_id, int
            ):
                raise TypeError("source message id must be an integer")
            if source_message_id <= 0:
                raise ValueError("source message id must be positive")
        if len(set(source_message_ids)) != len(source_message_ids):
            raise ValueError("source message ids must be distinct")

    @staticmethod
    def _validate_destination_message_id(destination_message_id: int) -> None:
        if isinstance(destination_message_id, bool) or not isinstance(
            destination_message_id, int
        ):
            raise TypeError("destination message id must be an integer")
        if destination_message_id <= 0:
            raise ValueError("destination message id must be positive")
