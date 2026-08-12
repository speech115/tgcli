"""Crash-recoverable per-peer archive deletion (ADR-0089)."""

from __future__ import annotations

import fcntl
import json
import shutil
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any

from tgcli import atomic, changes_cursor, safety, session
from tgcli.errors import NotFoundError, PartialFailure, PolicyError, TgcliError
from tgcli.session import ensure_state_dir, restrict_file, state_dir

_STATE_VERSION = 2


def _archive_job_blockers(alias: str) -> list[dict[str, Any]]:
    from tgcli.jobs import store as jobs_store

    if not jobs_store.path_for(alias).is_file():
        return []
    conn = jobs_store.connect_existing(alias)
    try:
        return [
            job
            for job in jobs_store.list_jobs(conn)
            if job["state"] in ("queued", "running")
            and str(job["kind"]).startswith("archive-")
        ]
    finally:
        conn.close()


def _require_no_archive_jobs(alias: str) -> None:
    blockers = _archive_job_blockers(alias)
    if not blockers:
        return
    names = ", ".join(
        f"{job['key']} ({job['kind']}, {job['state']})" for job in blockers
    )
    raise PolicyError(
        f"archive purge is blocked by active archive job(s): {names}; "
        "cancel each job with: tg jobs cancel KEY"
    )


def _hold_session_locks(stack: ExitStack, account) -> None:
    ensure_state_dir("sessions")
    roles: list[str | None] = [None, *session.list_roles(account)]
    for role in roles:
        session_file = session.session_path(account, role=role)
        lock_path = session_file.with_suffix(".lock")
        handle = stack.enter_context(lock_path.open("a+"))
        restrict_file(lock_path)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            label = session.session_label(account, role)
            raise PolicyError(
                f"archive purge is blocked because session {label!r} is busy; "
                "stop the active tg command first"
            ) from exc
        stack.callback(fcntl.flock, handle, fcntl.LOCK_UN)


def require_no_pending(account_dir: Path) -> None:
    """Block archive mutations until an interrupted purge is resumed."""
    markers = sorted(_marker_root(account_dir).glob("*.json"))
    if not markers:
        return
    peer_id = markers[0].stem
    raise PolicyError(
        "archive purge recovery is pending; resume it before archive work: "
        f"tg archive purge {peer_id} --confirm"
    )


@contextmanager
def operation_lock(
    account_dir: Path,
    alias: str,
    *,
    exclusive: bool = False,
    allow_pending: bool = False,
):
    """Exclude peer purge from archive-local work that can restore rows."""
    path = account_dir / "archive.lock"
    handle = path.open("a+")
    restrict_file(path)
    try:
        try:
            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(handle, mode | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PolicyError(
                f"archive is busy for account {alias!r}; retry after it finishes"
            ) from exc
        if not allow_pending:
            require_no_pending(account_dir)
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def exclusive(account_dir: Path, account):
    """Exclude archive jobs and other archive work for the destructive window."""
    from tgcli.jobs import store as jobs_store

    alias = account.alias
    try:
        with ExitStack() as stack:
            stack.enter_context(jobs_store.lane_lock(alias, "local"))
            stack.enter_context(jobs_store.lane_lock(alias, "telegram"))
            stack.enter_context(
                operation_lock(account_dir, alias, exclusive=True, allow_pending=True)
            )
            _hold_session_locks(stack, account)
            _require_no_archive_jobs(alias)
            yield
    except PolicyError as exc:
        blockers = _archive_job_blockers(alias)
        if blockers:
            _require_no_archive_jobs(alias)
        raise exc


def _identity(conn, peer_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT kind, title, username, chat_ref FROM scope WHERE peer_id = ?",
        (peer_id,),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT kind, title, username, chat_ref FROM sync_state WHERE peer_id = ?",
            (peer_id,),
        ).fetchone()
    if row is None:
        raise NotFoundError(f"chat not in archive scope or sync state: {peer_id!r}")
    kind = row["kind"]
    if kind == "user":
        raise PolicyError(
            "private 1:1 dialogs are standing archive scope and cannot be purged"
        )
    if kind not in ("group", "channel"):
        raise PolicyError(f"unsupported archive peer kind: {kind!r}")
    return {
        "peer_id": peer_id,
        "kind": kind,
        "title": row["title"],
        "username": row["username"],
        "chat_ref": row["chat_ref"],
    }


def _resolve_peer_id(conn, chat: str) -> int:
    """Resolve only durable purge identities, never message-only orphans."""
    raw = chat.strip()
    if not raw:
        raise PolicyError("archive purge CHAT must be non-empty")
    rows = conn.execute(
        "SELECT peer_id, chat_ref, username FROM scope "
        "UNION ALL "
        "SELECT peer_id, chat_ref, username FROM sync_state "
        "WHERE peer_id NOT IN (SELECT peer_id FROM scope) "
        "ORDER BY peer_id"
    ).fetchall()
    try:
        numeric = int(raw)
    except ValueError:
        numeric = None
    needle = raw.lstrip("@").casefold()
    matches: set[int] = set()
    for row in rows:
        peer_id = int(row["peer_id"])
        if numeric is not None and peer_id == numeric:
            matches.add(peer_id)
        for candidate in (row["chat_ref"], row["username"]):
            if (
                candidate is not None
                and str(candidate).lstrip("@").casefold() == needle
            ):
                matches.add(peer_id)
    if len(matches) == 1:
        return matches.pop()
    if len(matches) > 1:
        raise PolicyError(
            f"archive purge CHAT is ambiguous across peer ids {sorted(matches)}; "
            "retry with the intended numeric peer id"
        )
    raise NotFoundError(f"chat not in archive scope or sync state: {chat!r}")


def _file_stats(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    if path.is_file():
        return 1, path.stat().st_size
    files = 0
    size = 0
    for item in path.rglob("*"):
        if item.is_file():
            files += 1
            size += item.stat().st_size
    return files, size


def _download_paths(conn, account_dir: Path, peer: dict[str, Any]) -> list[Path]:
    peer_id = int(peer["peer_id"])
    rows = conn.execute(
        "SELECT message_id FROM messages WHERE peer_id = ? "
        "UNION SELECT message_id FROM revisions WHERE peer_id = ? "
        "UNION SELECT message_id FROM tombstones WHERE peer_id = ? "
        "UNION SELECT message_id FROM transcripts WHERE peer_id = ? "
        "ORDER BY message_id",
        (peer_id, peer_id, peer_id, peer_id),
    ).fetchall()
    root = state_dir() / "downloads"
    owned_root = (account_dir / "media" / str(peer_id)).resolve()
    paths: list[Path] = []
    for row in rows:
        message_id = int(row["message_id"])
        key = sha256(f"{peer_id}:{message_id}".encode()).hexdigest()
        state_path = root / f"{key}.json"
        try:
            checkpoint = json.loads(state_path.read_text())
            destination = Path(str(checkpoint["destination"])).expanduser().resolve()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if not destination.is_relative_to(owned_root):
            continue
        paths.append(state_path)
        part_path = root / f"{key}.part"
        if part_path.exists():
            paths.append(part_path)
    return paths


def _marker_root(account_dir: Path) -> Path:
    return account_dir / ".purge"


def _marker_path(account_dir: Path, peer_id: int) -> Path:
    return _marker_root(account_dir) / f"{peer_id}.json"


def _quarantine_path(account_dir: Path, peer_id: int) -> Path:
    return _marker_root(account_dir) / str(peer_id)


def _download_quarantine_path(alias: str, peer_id: int) -> Path:
    return state_dir() / "downloads" / ".purge" / alias / str(peer_id)


def _matches_chat(plan: dict[str, Any], chat: str) -> bool:
    peer = plan["peer"]
    needle = chat.strip().lstrip("@").casefold()
    candidates = (
        str(peer["peer_id"]),
        peer.get("chat_ref"),
        peer.get("username"),
    )
    return any(
        value is not None and str(value).strip().lstrip("@").casefold() == needle
        for value in candidates
    )


def _pending_record(account_dir: Path, chat: str) -> dict[str, Any] | None:
    root = _marker_root(account_dir)
    if not root.is_dir():
        return None
    for path in sorted(root.glob("*.json")):
        try:
            record = json.loads(path.read_text())
            if record.get("version") != _STATE_VERSION:
                raise ValueError
            plan = record["plan"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PolicyError(
                f"archive purge recovery state is invalid: {path}"
            ) from exc
        if _matches_chat(plan, chat):
            return record
    return None


def require_pending_matches(account_dir: Path, chat: str) -> None:
    markers = sorted(_marker_root(account_dir).glob("*.json"))
    if markers and _pending_record(account_dir, chat) is None:
        require_no_pending(account_dir)


def _fresh_preview(conn, account_dir: Path, chat: str) -> dict[str, Any]:
    peer_id = _resolve_peer_id(conn, chat)
    peer = _identity(conn, peer_id)
    tables = {
        "messages": "messages",
        "revisions": "revisions",
        "tombstones": "tombstones",
        "transcripts": "transcripts",
        "fts": "messages_fts",
        "scope": "scope",
        "sync_state": "sync_state",
    }
    rows = {
        name: int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE peer_id = ?", (peer_id,)
            ).fetchone()[0]
        )
        for name, table in tables.items()
    }
    files, size = _file_stats(account_dir / "media" / str(peer_id))
    for path in _download_paths(conn, account_dir, peer):
        count, byte_count = _file_stats(path)
        files += count
        size += byte_count
    return {
        "confirmed": False,
        "peer": peer,
        "rows": rows,
        "files": files,
        "bytes": size,
        "cleanup_pending": False,
    }


def preview(conn, account_dir: Path, chat: str) -> dict[str, Any]:
    """Describe exactly what one offline purge would remove or resume."""
    pending = _pending_record(account_dir, chat)
    if pending is not None:
        return pending["plan"] | {"confirmed": False, "cleanup_pending": True}
    return _fresh_preview(conn, account_dir, chat)


def _write_marker(
    account_dir: Path, plan: dict[str, Any], download_paths: list[Path]
) -> dict[str, Any]:
    peer_id = int(plan["peer"]["peer_id"])
    root = _marker_root(account_dir)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    states = {path.stem: path for path in download_paths if path.suffix == ".json"}
    parts = {path.stem: path for path in download_paths if path.suffix == ".part"}
    record = {
        "version": _STATE_VERSION,
        "plan": plan,
        "downloads": [
            {
                "state_name": state.name,
                "part_name": parts[key].name if key in parts else None,
                "state_sha256": sha256(state.read_bytes()).hexdigest(),
            }
            for key, state in sorted(states.items())
        ],
    }
    atomic.replace_text(
        _marker_path(account_dir, peer_id),
        json.dumps(record, ensure_ascii=False, separators=(",", ":")),
    )
    return record


def _move_if_present(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    if destination.exists():
        raise PolicyError(
            f"archive purge quarantine target already exists: {destination}"
        )
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    source.replace(destination)


def _quarantine(account_dir: Path, alias: str, record: dict[str, Any]) -> None:
    peer_id = int(record["plan"]["peer"]["peer_id"])
    target = _quarantine_path(account_dir, peer_id)
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    _move_if_present(account_dir / "media" / str(peer_id), target / "media")
    downloads = state_dir() / "downloads"
    download_target = _download_quarantine_path(alias, peer_id)
    for checkpoint in record.get("downloads") or []:
        state_name = str(checkpoint["state_name"])
        source_state = downloads / state_name
        if source_state.exists():
            current_digest = sha256(source_state.read_bytes()).hexdigest()
            if current_digest != checkpoint["state_sha256"]:
                continue
        part_name = checkpoint.get("part_name")
        if part_name:
            source_part = downloads / str(part_name)
            target_part = download_target / str(part_name)
            if source_part.exists() and not target_part.exists():
                _move_if_present(source_part, target_part)
        target_state = download_target / state_name
        if source_state.exists() and not target_state.exists():
            _move_if_present(source_state, target_state)


def _without_peer_account_state(
    conn, peer_id: int
) -> tuple[str | None, str | None, str | None]:
    row = conn.execute(
        "SELECT changes_cursor, gap_json, reconcile_json FROM account_sync WHERE id = 1"
    ).fetchone()
    if row is None:
        return None, None, None
    cursor_text = row["changes_cursor"]
    if cursor_text:
        cursor = changes_cursor.decode(str(cursor_text))
        if peer_id in cursor.channels:
            cursor_text = changes_cursor.encode(
                changes_cursor.without_channel(cursor, peer_id)
            )
    gap_json = row["gap_json"]
    if gap_json:
        gap = json.loads(gap_json)
        if isinstance(gap, dict) and gap.get("scope") == peer_id:
            gap_json = None
    reconcile_json = row["reconcile_json"]
    if reconcile_json:
        reconcile = json.loads(reconcile_json)
        if isinstance(reconcile, dict):
            comparisons = [
                item
                for item in reconcile.get("comparisons") or []
                if not isinstance(item, dict) or item.get("peer_id") != peer_id
            ]
            reconcile["comparisons"] = comparisons
            reconcile["sampled"] = len(comparisons)
            reconcile["mismatched"] = sum(
                1
                for item in comparisons
                if item.get("telegram") is not None
                and item.get("telegram") != item.get("local")
            )
            reconcile_json = json.dumps(
                reconcile, ensure_ascii=False, separators=(",", ":")
            )
    return cursor_text, gap_json, reconcile_json


def _delete_peer(conn, peer_id: int) -> None:
    cursor_text, gap_json, reconcile_json = _without_peer_account_state(conn, peer_id)
    with conn:
        for table in (
            "messages_fts",
            "transcripts",
            "revisions",
            "tombstones",
            "messages",
            "sync_state",
            "scope",
        ):
            conn.execute(f"DELETE FROM {table} WHERE peer_id = ?", (peer_id,))
        conn.execute(
            "UPDATE account_sync SET changes_cursor = ?, gap_json = ?, "
            "reconcile_json = ? WHERE id = 1",
            (cursor_text, gap_json, reconcile_json),
        )


def _cleanup(account_dir: Path, alias: str, peer_id: int) -> None:
    quarantine = _quarantine_path(account_dir, peer_id)
    if quarantine.exists():
        shutil.rmtree(quarantine)
    download_quarantine = _download_quarantine_path(alias, peer_id)
    if download_quarantine.exists():
        shutil.rmtree(download_quarantine)
    for directory in (download_quarantine.parent, download_quarantine.parent.parent):
        try:
            directory.rmdir()
        except OSError:
            pass
    _marker_path(account_dir, peer_id).unlink(missing_ok=True)
    try:
        _marker_root(account_dir).rmdir()
    except OSError:
        pass


def result_rows(data: dict[str, Any]) -> list[tuple[str, Any]]:
    """Stable two-column plain result for previews and partial cleanup."""
    peer = data["peer"]
    rows = data["rows"]
    return [
        ("confirmed", data["confirmed"]),
        ("cleanup_pending", data["cleanup_pending"]),
        ("peer_id", peer["peer_id"]),
        ("kind", peer["kind"]),
        *(
            (name, rows[name])
            for name in (
                "messages",
                "revisions",
                "tombstones",
                "transcripts",
                "fts",
                "scope",
                "sync_state",
            )
        ),
        ("files", data["files"]),
        ("bytes", data["bytes"]),
    ]


def _append_audit(alias: str, plan: dict[str, Any]) -> None:
    peer_id = int(plan["peer"]["peer_id"])
    safety.append_audit(
        "archive-purge",
        alias,
        {
            "peer_id": peer_id,
            "kind": plan["peer"]["kind"],
            "rows": sum(int(value) for value in plan["rows"].values()),
            "files": int(plan["files"]),
            "bytes": int(plan["bytes"]),
        },
    )


def commit(conn, account_dir: Path, alias: str, chat: str) -> dict[str, Any]:
    """Quarantine files, commit database deletion, then reap the quarantine."""
    record = _pending_record(account_dir, chat)
    if record is None:
        plan = _fresh_preview(conn, account_dir, chat)
        download_paths = _download_paths(conn, account_dir, plan["peer"])
        _append_audit(alias, plan)
        record = _write_marker(account_dir, plan, download_paths)
    else:
        plan = record["plan"]
        _append_audit(alias, plan)
    plan = record["plan"]
    peer_id = int(plan["peer"]["peer_id"])
    _quarantine(account_dir, alias, record)
    _delete_peer(conn, peer_id)
    data = plan | {
        "confirmed": True,
        "cleanup_pending": False,
        "account": {"alias": alias},
    }
    try:
        _cleanup(account_dir, alias, peer_id)
    except OSError as exc:
        data["cleanup_pending"] = True
        raise PartialFailure(
            "archive peer data was purged but quarantine cleanup is pending",
            data,
            cause=TgcliError(str(exc)),
            rows=result_rows(data),
        ) from exc
    return data
