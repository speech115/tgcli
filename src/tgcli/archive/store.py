"""Per-account archive SQLite/WAL store facade (ADR-0068/0116).

Persistence lives in peer modules; this module re-exports the historical
``tgcli.archive.store`` public surface so callers stay stable.
"""

from __future__ import annotations

from tgcli.archive.messages import (
    counts,
    find_message_peers,
    insert_tombstone,
    upsert_message,
)
from tgcli.archive.peers import list_sync_identity, resolve as resolve_peer_identity
from tgcli.archive.schema import (
    BUSY_TIMEOUT_MS,
    DB_NAME,
    NO_TRANSCRIPT_MARKER,
    SCHEMA_VERSION,
    TRANSCRIBABLE_MEDIA_KINDS,
    connect,
    db_path_for,
    default_archive_root,
    ensure_account_dir,
    ensure_meta,
    fold_yo,
    integrity_report,
    read_meta,
    require_bound_alias,
    require_bound_user,
    schema_version,
)
from tgcli.archive.sync_state import (
    add_scope,
    get_sync_state,
    in_explicit_scope,
    list_scope,
    list_sync_state,
    read_account_sync,
    remove_scope,
    upsert_sync_state,
    write_account_sync,
)
from tgcli.archive.transcripts import (
    ensure_transcript_queue,
    list_transcript_queue,
    record_transcript_failure,
    record_transcript_success,
    replace_fts_row,
    transcript_errors,
    transcript_row,
    transcript_status_counts,
)

__all__ = [
    "BUSY_TIMEOUT_MS",
    "DB_NAME",
    "NO_TRANSCRIPT_MARKER",
    "SCHEMA_VERSION",
    "TRANSCRIBABLE_MEDIA_KINDS",
    "add_scope",
    "connect",
    "counts",
    "db_path_for",
    "default_archive_root",
    "ensure_account_dir",
    "ensure_meta",
    "ensure_transcript_queue",
    "find_message_peers",
    "fold_yo",
    "get_sync_state",
    "in_explicit_scope",
    "insert_tombstone",
    "integrity_report",
    "list_scope",
    "list_sync_identity",
    "list_sync_state",
    "list_transcript_queue",
    "read_account_sync",
    "read_meta",
    "record_transcript_failure",
    "record_transcript_success",
    "remove_scope",
    "replace_fts_row",
    "require_bound_alias",
    "require_bound_user",
    "resolve_peer_identity",
    "schema_version",
    "transcript_errors",
    "transcript_row",
    "transcript_status_counts",
    "upsert_message",
    "upsert_sync_state",
    "write_account_sync",
]
