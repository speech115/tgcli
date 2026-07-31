"""Archive media paths and bounded acquisition retry state (ADR-0068)."""

from __future__ import annotations

from datetime import UTC, datetime

from tgcli.archive import store as store_mod

MAX_MEDIA_ATTEMPTS = 3
MEDIA_DIR_NAME = "media"


def media_relative_path(
    peer_id: int, message_id: int, *, media_kind: str, mime: str | None = None
) -> str:
    suffix = ".bin"
    if media_kind == "voice":
        suffix = ".ogg"
    elif media_kind == "video_note":
        suffix = ".mp4"
    elif mime:
        suffix = {
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "video/mp4": ".mp4",
        }.get(mime, suffix)
    return f"{MEDIA_DIR_NAME}/{peer_id}/{message_id}{suffix}"


def set_media_path(
    conn,
    peer_id: int,
    message_id: int,
    *,
    path: str,
    media_kind: str,
) -> None:
    """Publish a media path and close its acquisition failure episode."""
    store_mod.ensure_transcript_queue(conn, peer_id, message_id, media_kind=media_kind)
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE transcripts SET media_path = ?, media_kind = ?, "
        "media_attempts = 0, media_status = 'done', status = CASE "
        "WHEN status = 'no_transcript' THEN 'pending' ELSE status END, "
        "last_error = NULL, updated_at = ? "
        "WHERE peer_id = ? AND message_id = ?",
        (path, media_kind, now, peer_id, message_id),
    )
    message = conn.execute(
        "SELECT text FROM messages WHERE peer_id = ? AND message_id = ?",
        (peer_id, message_id),
    ).fetchone()
    store_mod.replace_fts_row(
        conn,
        peer_id,
        message_id,
        message["text"] if message is not None else "",
    )


def record_media_failure(conn, peer_id: int, message_id: int, *, error: str) -> None:
    """Count one acquisition failure and stop retrying after the hard cap."""
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE transcripts SET media_path = NULL, "
        "media_attempts = media_attempts + 1, "
        "media_status = CASE WHEN media_attempts + 1 >= ? "
        "THEN 'no_media' ELSE 'retryable' END, "
        "last_error = ?, updated_at = ? "
        "WHERE peer_id = ? AND message_id = ?",
        (MAX_MEDIA_ATTEMPTS, error, now, peer_id, message_id),
    )
