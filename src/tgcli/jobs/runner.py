"""Foreground lane runner for typed jobs (ADR-0087)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from telethon import errors as telethon_errors

from tgcli import desktop
from tgcli.clone import legs
from tgcli.commands import (
    archive as archive_cmd,
    archive_jobs as archive_jobs_cmd,
    clone as clone_cmd,
)
from tgcli.config import Config
from tgcli.errors import (
    ConfigError,
    NotFoundError,
    PartialFailure,
    PolicyError,
    RateLimitError,
)
from tgcli.jobs import store


def _error(exc: BaseException) -> dict[str, Any]:
    return {
        "code": getattr(exc, "code", "RUNTIME"),
        "message": (str(exc).strip() or type(exc).__name__)[:500],
    }


def _outcome(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": job["key"],
        "generation": job["generation"],
        "state": job["state"],
        "result": job["last_result"],
        "error": job["last_error"],
        "not_before": job["not_before"],
    }


def _notify_failed(job: dict[str, Any]) -> None:
    if job["state"] != "failed":
        return
    key = str(job["key"])
    try:
        desktop.notify("tgcli job failed", f"{key}; run: tg jobs show {key}")
    except Exception:
        pass


def _run_transcription(job: dict[str, Any], alias: str, config: Config) -> dict:
    return archive_cmd.transcribe(
        alias,
        limit=1,
        max_attempts=int(job["spec"]["max_attempts"]),
        config=config,
    )


def _progress_token(
    job: dict[str, Any],
    alias: str,
    config: Config | None,
    user_id: int,
    source_peer_id: int | None,
) -> dict:
    if job["kind"] in ("archive-backfill", "archive-sync"):
        return archive_jobs_cmd.progress_token(alias, config)
    if job["kind"] == "clone-sync":
        if source_peer_id is None:
            raise PolicyError("clone progress requires a resolved source identity")
        return clone_cmd.progress_token(user_id, source_peer_id)
    raise PolicyError(f"unsupported telegram job kind: {job['kind']}")


async def _run_telegram_job(
    tg,
    job: dict[str, Any],
    alias: str,
    config: Config | None,
    *,
    should_stop,
) -> dict:
    spec = job["spec"]
    if job["kind"] == "archive-backfill":
        return await archive_jobs_cmd.backfill_quantum(
            tg,
            alias,
            chats=list(spec["chats"]),
            private=bool(spec["private"]),
            limit=int(spec["limit"]),
            config=config,
        )
    if job["kind"] == "archive-sync":
        return await archive_cmd.sync(
            tg,
            alias,
            max_events=int(spec["max_events"]),
            max_dialogs=int(spec["max_dialogs"]),
            max_media=int(spec["max_media"]),
            config=config,
            should_stop=should_stop,
        )
    if job["kind"] == "clone-sync":
        return await clone_cmd.sync_text(
            tg,
            str(spec["source"]),
            alias,
            limit=legs.WINDOW,
        )
    raise PolicyError(f"unsupported telegram job kind: {job['kind']}")


def run_local(
    alias: str,
    *,
    max_runtime: float,
    config: Config,
    monotonic=time.monotonic,
    wall_clock=lambda: datetime.now(UTC),
) -> dict[str, Any]:
    started = monotonic()
    counts = {
        "selected": 0,
        "completed": 0,
        "queued": 0,
        "failed": 0,
        "cancelled": 0,
    }
    outcomes: list[dict[str, Any]] = []
    recovered = {"queued": 0, "cancelled": 0}
    stop_reason = "idle"
    with store.lane_lock(alias, "local"):
        conn = store.connect_existing(alias)
        try:
            recovered = store.recover_running(conn, "local", now=wall_clock())
            while monotonic() - started < max_runtime:
                job = store.claim_next(conn, "local", now=wall_clock())
                if job is None:
                    break
                counts["selected"] += 1
                result: dict | None = None
                try:
                    if job["kind"] != "archive-transcribe":
                        raise PolicyError(f"unsupported local job kind: {job['kind']}")
                    result = _run_transcription(job, alias, config)
                    if store.cancel_requested(conn, job):
                        final = store.finish_cancelled(
                            conn, job, result, now=wall_clock()
                        )
                    elif bool(result["remaining"]):
                        final = store.requeue(
                            conn,
                            job,
                            result=result,
                            reason="remaining",
                            now=wall_clock(),
                        )
                    else:
                        final = store.complete(conn, job, result, now=wall_clock())
                except (PolicyError, ConfigError, NotFoundError) as exc:
                    final = store.fail_terminal(
                        conn, job, _error(exc), now=wall_clock()
                    )
                except Exception as exc:
                    final = store.fail_runtime(conn, job, _error(exc), now=wall_clock())
                counts[final["state"]] += 1
                outcomes.append(_outcome(final))
                _notify_failed(final)
            else:
                stop_reason = "wall_clock_cap"
        finally:
            conn.close()
    return {
        "account": {"alias": alias},
        "lane": "local",
        **counts,
        "recovered": recovered,
        "outcomes": outcomes,
        "stop_reason": stop_reason,
    }


async def run_telegram(
    tg,
    alias: str,
    *,
    max_runtime: float,
    config: Config | None,
    monotonic=time.monotonic,
    wall_clock=lambda: datetime.now(UTC),
) -> dict[str, Any]:
    started = monotonic()
    counts = {
        "selected": 0,
        "completed": 0,
        "queued": 0,
        "failed": 0,
        "cancelled": 0,
    }
    outcomes: list[dict[str, Any]] = []
    recovered = {"queued": 0, "cancelled": 0}
    stop_reason = "idle"
    conn = store.connect_existing(alias)
    try:
        me = await tg.get_me()
        user_id = int(me.id)
        store.bind_user(conn, user_id)
        recovered = store.recover_running(conn, "telegram", now=wall_clock())
        while monotonic() - started < max_runtime:
            claimed = store.claim_next(conn, "telegram", now=wall_clock())
            if claimed is None:
                break
            job: dict[str, Any] = claimed
            counts["selected"] += 1
            before: dict | None = None
            source_peer_id: int | None = None
            halt_after = False
            try:
                if job["kind"] == "clone-sync":
                    source_peer_id = await clone_cmd.resolve_source_peer_id(
                        tg,
                        str(job["spec"]["source"]),
                        user_id,
                    )
                before = _progress_token(
                    job,
                    alias,
                    config,
                    user_id,
                    source_peer_id,
                )
                result = await _run_telegram_job(
                    tg,
                    job,
                    alias,
                    config,
                    should_stop=lambda: (
                        store.cancel_requested(conn, job)
                        or monotonic() - started >= max_runtime
                    ),
                )
                if store.cancel_requested(conn, job):
                    final = store.finish_cancelled(conn, job, result, now=wall_clock())
                elif bool(result["remaining"]):
                    final = store.requeue(
                        conn,
                        job,
                        result=result,
                        reason=str(result.get("stop_reason") or "remaining"),
                        now=wall_clock(),
                    )
                else:
                    final = store.complete(conn, job, result, now=wall_clock())
            except PartialFailure as exc:
                cause = exc.cause
                if isinstance(cause, (PolicyError, ConfigError, NotFoundError)):
                    final = store.fail_terminal(
                        conn, job, _error(cause), now=wall_clock()
                    )
                else:
                    final = store.fail_runtime(
                        conn, job, _error(cause), now=wall_clock()
                    )
            except RateLimitError as exc:
                retry_after = int(exc.details.get("retry_after") or 0)
                result = {
                    "remaining": True,
                    "retry_after": retry_after,
                    "stop_reason": "cooldown_deferred",
                }
                final = store.requeue(
                    conn,
                    job,
                    result=result,
                    reason="rate_limit",
                    now=wall_clock(),
                    not_before=wall_clock() + timedelta(seconds=retry_after),
                )
                stop_reason = "cooldown_deferred"
            except telethon_errors.FloodWaitError as exc:
                retry_after = int(exc.seconds)
                result = {
                    "remaining": True,
                    "retry_after": retry_after,
                    "stop_reason": "cooldown_deferred",
                }
                final = store.requeue(
                    conn,
                    job,
                    result=result,
                    reason="rate_limit",
                    now=wall_clock(),
                    not_before=wall_clock() + timedelta(seconds=retry_after),
                )
                stop_reason = "cooldown_deferred"
            except (PolicyError, ConfigError, NotFoundError) as exc:
                final = store.fail_terminal(conn, job, _error(exc), now=wall_clock())
            except (
                telethon_errors.AuthKeyError,
                telethon_errors.UnauthorizedError,
            ) as exc:
                final = store.fail_terminal(conn, job, _error(exc), now=wall_clock())
            except Exception as exc:
                error = _error(exc)
                try:
                    after = _progress_token(
                        job,
                        alias,
                        config,
                        user_id,
                        source_peer_id,
                    )
                except Exception:
                    after = before
                if before is not None and after != before:
                    final = store.requeue(
                        conn,
                        job,
                        result={
                            "error": error,
                            "progress": after,
                            "remaining": True,
                        },
                        reason="progress_before_error",
                        now=wall_clock(),
                    )
                    halt_after = True
                else:
                    final = store.fail_runtime(conn, job, error, now=wall_clock())
            counts[final["state"]] += 1
            outcomes.append(_outcome(final))
            _notify_failed(final)
            result_stop = (final.get("last_result") or {}).get("stop_reason")
            if result_stop is not None or stop_reason != "idle" or halt_after:
                stop_reason = str(result_stop or stop_reason)
                break
        else:
            stop_reason = "wall_clock_cap"
    finally:
        conn.close()
    return {
        "account": {"alias": alias, "user_id": user_id},
        "lane": "telegram",
        **counts,
        "recovered": recovered,
        "outcomes": outcomes,
        "stop_reason": stop_reason,
    }
