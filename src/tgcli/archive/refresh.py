"""One-shot archive refresh orchestration and failure reporting (ADR-0070)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from tgcli import desktop
from tgcli.archive import (
    store as store_mod,
    sync as sync_mod,
    transcribe as transcribe_mod,
)
from tgcli.errors import PartialFailure, RateLimitError, TgcliError

FAILURE_NOTIFICATION_THRESHOLD = 3


def _error_text(exc: BaseException) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}:{detail[:500]}" if detail else type(exc).__name__


def _refresh_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "failure_streak": state["refresh_failure_streak"],
        "last_error": state["refresh_last_error"],
        "notification_sent": state["refresh_notification_sent"],
        "notification_threshold": FAILURE_NOTIFICATION_THRESHOLD,
    }


def record_refresh_failure(conn: sqlite3.Connection, *, error: str) -> dict[str, Any]:
    """Increment the account-level failure streak for one refresh run."""
    existing = store_mod.read_account_sync(conn)
    streak = int(existing["refresh_failure_streak"]) + 1
    with conn:
        conn.execute(
            "INSERT INTO account_sync("
            "id, refresh_failure_streak, refresh_last_error, "
            "refresh_notification_sent) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "refresh_failure_streak = excluded.refresh_failure_streak, "
            "refresh_last_error = excluded.refresh_last_error",
            (streak, error, int(existing["refresh_notification_sent"])),
        )
    return store_mod.read_account_sync(conn)


def mark_refresh_notification_sent(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(
            "INSERT INTO account_sync(id, refresh_notification_sent) VALUES (1, 1) "
            "ON CONFLICT(id) DO UPDATE SET refresh_notification_sent = 1"
        )


def record_refresh_success(conn: sqlite3.Connection) -> dict[str, Any]:
    """Clear the failure episode after a fully successful refresh."""
    with conn:
        conn.execute(
            "INSERT INTO account_sync("
            "id, refresh_failure_streak, refresh_last_error, "
            "refresh_notification_sent) VALUES (1, 0, NULL, 0) "
            "ON CONFLICT(id) DO UPDATE SET "
            "refresh_failure_streak = 0, refresh_last_error = NULL, "
            "refresh_notification_sent = 0"
        )
    return store_mod.read_account_sync(conn)


def _record_failure(conn: sqlite3.Connection, error: str) -> dict[str, Any]:
    state = record_refresh_failure(conn, error=error)
    if (
        state["refresh_failure_streak"] >= FAILURE_NOTIFICATION_THRESHOLD
        and not state["refresh_notification_sent"]
        and desktop.notify(
            "tgcli archive refresh",
            "Archive refresh failed repeatedly; run tg archive status",
        )
    ):
        mark_refresh_notification_sent(conn)
        state = store_mod.read_account_sync(conn)
    return state


async def run(
    tg,
    conn: sqlite3.Connection,
    *,
    account_alias: str,
    account_user_id: int,
    account_dir: Path,
    max_events: int,
    max_dialogs: int,
    max_media: int,
    transcribe_limit: int,
    max_attempts: int,
) -> dict[str, Any]:
    """Run sync (including media) and local transcription as one bounded job."""
    from tgcli.governor import pacing

    deferred: list[str] = []
    sync_data = None
    remaining = pacing.wall_clock_remaining()
    if remaining is not None and remaining <= 0:
        # --max-runtime already exhausted before dispatch: a normal stop,
        # like the cooldown-deferred path (ADR-0072 decision 6).
        deferred.append("sync")
    elif sync_types_cooling(tg):
        # ADR-0072 decision 4 / plan phase 6: a scheduled pass waking into a
        # partial cooldown does what the free request types allow, reports
        # the rest as deferred, and exits 0 — not the old exit 5 on every
        # wake. Transcription is local and always free.
        deferred.append("sync")
    else:
        try:
            sync_data = await sync_mod.sync_archive(
                tg,
                conn,
                account_user_id=account_user_id,
                max_events=max_events,
                max_dialogs=max_dialogs,
                max_media=max_media,
                account_alias=account_alias,
                account_dir=account_dir,
            )
        except RateLimitError:
            raise
        except Exception as exc:
            _record_failure(conn, _error_text(exc))
            raise
    transcribe_data = transcribe_mod.run_queue(
        conn,
        account_dir,
        limit=transcribe_limit,
        max_attempts=max_attempts,
    )

    data: dict[str, Any] = {"sync": sync_data, "transcribe": transcribe_data}
    if deferred:
        data["deferred"] = deferred
        data["stop_reason"] = (
            "wall_clock_cap"
            if remaining is not None and remaining <= 0
            else "cooldown_deferred"
        )
        state = store_mod.read_account_sync(conn)
        data["refresh"] = _refresh_state(state)
        return data
    assert sync_data is not None
    media_failures = (sync_data.get("media") or {}).get("failed") or []
    transcript_failures = transcribe_data.get("errors") or []
    missing_media = int(transcribe_data.get("skipped_missing_media") or 0)
    if media_failures or transcript_failures or missing_media:
        # The pipeline completed; item failures are reported in stage data but
        # must not poison the account-level outage episode.
        state = record_refresh_success(conn)
        data["refresh"] = _refresh_state(state)
        raise PartialFailure(
            "archive refresh completed with item failures",
            data,
            cause=TgcliError("archive refresh completed with item failures"),
        )

    state = record_refresh_success(conn)
    data["refresh"] = _refresh_state(state)
    return data


def sync_types_cooling(tg) -> bool:
    """Whether the request types sync depends on are cooling right now.

    The governor keys cooldowns per Telegram request type (ADR-0072
    decision 1); a scheduled refresh checks the ledger before dispatching
    so a hot account is reported as deferred rather than failing. The set
    covers everything the sync path actually sends: the changes poll,
    channel catch-ups, entity resolution, and media acquisition.
    """
    from tgcli.governor import pacing

    governor = pacing.governor_of(tg)
    if governor is None:
        return False
    ledger, account = governor
    active = ledger.active_cooldowns(account)
    sync_types = {
        "updates.GetDifferenceRequest",
        "updates.GetChannelDifferenceRequest",
        "messages.GetHistoryRequest",
        "messages.GetMessagesRequest",
        "messages.GetDialogsRequest",
        "users.GetUsersRequest",
        "contacts.ResolveUsernameRequest",
        "channels.GetFullChannelRequest",
        "upload.GetFileRequest",
    }
    return bool(active.keys() & sync_types)
