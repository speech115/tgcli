"""Per-clone SQLite/WAL state backend (ADR-0060).

Owns schema, open/close, full and dirty-tracked writes, and integrity
checks. ``clone.state`` keeps the public ``CloneState`` / ``load`` /
``save`` seam; this module is the file format behind it.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tgcli.errors import PolicyError
from tgcli.session import restrict_file

SCHEMA_VERSION = 1

_META_FIELDS = (
    "version",
    "account_user_id",
    "source_peer_id",
    "source_title",
    "source_kind",
    "destination_kind",
    "destination_peer_id",
    "creation_marker",
    "cursor",
    "retry_not_before",
    "created_at",
    "last_synced_at",
    "discussion_source_peer_id",
    "discussion_destination_peer_id",
    "discussion_linked",
    "discussion_cursor",
    "comments",
    "pinned_dest_id",
    "pin_occupied",
)

_MAP_TABLES = (
    "id_map",
    "discussion_id_map",
    "topic_map",
    "avatar_photo_ids",
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL,
    account_user_id INTEGER NOT NULL,
    source_peer_id INTEGER NOT NULL,
    source_title TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    destination_kind TEXT NOT NULL,
    destination_peer_id INTEGER,
    creation_marker TEXT,
    cursor INTEGER NOT NULL,
    retry_not_before TEXT,
    created_at TEXT NOT NULL,
    last_synced_at TEXT,
    discussion_source_peer_id INTEGER,
    discussion_destination_peer_id INTEGER,
    discussion_linked INTEGER NOT NULL,
    discussion_cursor INTEGER NOT NULL,
    comments TEXT NOT NULL,
    pinned_dest_id INTEGER,
    pin_occupied INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS id_map (
    source INTEGER PRIMARY KEY,
    dest INTEGER NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS discussion_id_map (
    source INTEGER PRIMARY KEY,
    dest INTEGER NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS topic_map (
    source INTEGER PRIMARY KEY,
    dest INTEGER NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS avatar_photo_ids (
    source INTEGER PRIMARY KEY,
    dest INTEGER NOT NULL UNIQUE
);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open (or create) a clone DB with the ADR-0060 pragmas and schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if created or version == 0:
            conn.executescript(_SCHEMA_SQL)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
        elif version != SCHEMA_VERSION:
            conn.close()
            raise PolicyError(
                f"clone state {path.name} has unsupported schema version "
                f"{version}; expected {SCHEMA_VERSION}"
            )
        _require_integrity(conn, path)
    except PolicyError:
        raise
    except sqlite3.Error as exc:
        conn.close()
        raise PolicyError(
            f"clone state {path.name} is corrupted; manual repair is required"
        ) from exc
    if created:
        restrict_file(path)
    return conn


def integrity_report(conn: sqlite3.Connection) -> str:
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    if len(rows) == 1 and rows[0][0] == "ok":
        return "ok"
    return "; ".join(str(row[0]) for row in rows)


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _require_integrity(conn: sqlite3.Connection, path: Path) -> None:
    report = integrity_report(conn)
    if report != "ok":
        conn.close()
        raise PolicyError(
            f"clone state {path.name} failed integrity check ({report}); "
            "manual repair is required"
        )


def read_dict(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM meta WHERE id = 1").fetchone()
    if row is None:
        raise PolicyError("clone state database has no meta row")
    data: dict[str, Any] = {name: row[name] for name in _META_FIELDS}
    data["discussion_linked"] = bool(data["discussion_linked"])
    data["pin_occupied"] = bool(data["pin_occupied"])
    for table, key in (
        ("id_map", "id_map"),
        ("discussion_id_map", "discussion_id_map"),
        ("topic_map", "topic_map"),
        ("avatar_photo_ids", "avatar_photo_ids"),
    ):
        data[key] = {
            str(source): int(dest)
            for source, dest in conn.execute(f"SELECT source, dest FROM {table}")
        }
    return data


def write_full(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    """Replace the entire database contents with ``data`` (one transaction)."""
    with conn:
        for table in _MAP_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM meta")
        _upsert_meta(conn, data)
        for table, key in (
            ("id_map", "id_map"),
            ("discussion_id_map", "discussion_id_map"),
            ("topic_map", "topic_map"),
            ("avatar_photo_ids", "avatar_photo_ids"),
        ):
            rows = [(int(k), int(v)) for k, v in data.get(key, {}).items()]
            conn.executemany(f"INSERT INTO {table}(source, dest) VALUES (?, ?)", rows)


def write_dirty(
    conn: sqlite3.Connection,
    data: dict[str, Any],
    *,
    dirty_id_map: set[str],
    dirty_discussion_id_map: set[str],
    dirty_topic_map: set[str],
    dirty_avatar_photo_ids: set[str],
    deleted_discussion_id_map: set[str] | None = None,
) -> None:
    """Commit meta + only the dirty map keys in one transaction."""
    with conn:
        _upsert_meta(conn, data)
        _upsert_map_keys(conn, "id_map", data.get("id_map", {}), dirty_id_map)
        _upsert_map_keys(
            conn,
            "discussion_id_map",
            data.get("discussion_id_map", {}),
            dirty_discussion_id_map,
        )
        if deleted_discussion_id_map:
            conn.executemany(
                "DELETE FROM discussion_id_map WHERE source = ?",
                [(int(key),) for key in deleted_discussion_id_map],
            )
        _upsert_map_keys(conn, "topic_map", data.get("topic_map", {}), dirty_topic_map)
        _upsert_map_keys(
            conn,
            "avatar_photo_ids",
            data.get("avatar_photo_ids", {}),
            dirty_avatar_photo_ids,
        )


def _upsert_meta(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    cols = ", ".join(("id", *_META_FIELDS))
    placeholders = ", ".join("?" for _ in range(len(_META_FIELDS) + 1))
    values: list[Any] = [1]
    for name in _META_FIELDS:
        value = data[name]
        if name in {"discussion_linked", "pin_occupied"}:
            value = int(bool(value))
        values.append(value)
    conn.execute(
        f"INSERT OR REPLACE INTO meta ({cols}) VALUES ({placeholders})",
        values,
    )


def _upsert_map_keys(
    conn: sqlite3.Connection,
    table: str,
    mapping: dict[str, int],
    dirty: set[str],
) -> None:
    rows = [(int(key), int(mapping[key])) for key in dirty if key in mapping]
    if rows:
        conn.executemany(
            f"INSERT OR REPLACE INTO {table}(source, dest) VALUES (?, ?)",
            rows,
        )


def sidecar_paths(db_path: Path) -> tuple[Path, Path]:
    return Path(f"{db_path}-wal"), Path(f"{db_path}-shm")


def load_dict(db_path: Path) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        return read_dict(conn)
    finally:
        conn.close()


def persist(
    db_path: Path,
    data: dict[str, Any],
    *,
    full: bool,
    dirty_id_map: set[str] | None = None,
    dirty_discussion_id_map: set[str] | None = None,
    dirty_topic_map: set[str] | None = None,
    dirty_avatar_photo_ids: set[str] | None = None,
    deleted_discussion_id_map: set[str] | None = None,
) -> None:
    conn = connect(db_path)
    try:
        if full:
            write_full(conn, data)
        else:
            write_dirty(
                conn,
                data,
                dirty_id_map=dirty_id_map or set(),
                dirty_discussion_id_map=dirty_discussion_id_map or set(),
                dirty_topic_map=dirty_topic_map or set(),
                dirty_avatar_photo_ids=dirty_avatar_photo_ids or set(),
                deleted_discussion_id_map=deleted_discussion_id_map or set(),
            )
    finally:
        conn.close()
    restrict_file(db_path)


def read_json_file(json_path: Path, *, expected_version: int) -> dict[str, Any]:
    """Read and version-check a legacy v2 JSON state file."""
    try:
        raw = json_path.read_text()
    except OSError as exc:
        raise PolicyError(
            f"clone state {json_path.name} is unreadable; manual repair is required"
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolicyError(
            f"clone state {json_path.name} is corrupted; manual repair is required"
        ) from exc
    if type(data) is not dict:
        raise PolicyError(
            f"clone state {json_path.name} is invalid; manual repair is required"
        )
    if data.get("version") != expected_version:
        raise PolicyError(
            f"clone state {json_path.name} has unsupported version "
            f"{data.get('version')!r}; expected {expected_version}"
        )
    return data


def finish_json_import(json_path: Path, db_path: Path, data: dict[str, Any]) -> None:
    """Write validated data to SQLite and rename the JSON to ``.imported``."""
    persist(db_path, data, full=True)
    os.replace(json_path, json_path.with_name(json_path.name + ".imported"))


def probe_paths(db_path: Path, json_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        if json_path.exists():
            return {"schema_version": None, "integrity": "json-pending-import"}
        return {"schema_version": None, "integrity": "missing"}
    try:
        conn = connect(db_path)
    except PolicyError as exc:
        return {"schema_version": None, "integrity": str(exc)}
    try:
        return {
            "schema_version": schema_version(conn),
            "integrity": integrity_report(conn),
        }
    finally:
        conn.close()


def archive_paths(*paths: Path) -> list[Path]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    archived: list[Path] = []
    for path in paths:
        if path.exists():
            target = path.with_name(f"{path.name}.superseded-{stamp}")
            os.replace(path, target)
            archived.append(target)
    return archived
