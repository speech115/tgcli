"""Foreground lane runner for typed jobs (ADR-0087)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
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
from tgcli.formatting import mask_phones_in_text
from tgcli.jobs import model, store

ProcessJob = Callable[..., tuple[dict[str, Any], str | None]]
AsyncProcessJob = Callable[..., Awaitable[tuple[dict[str, Any], str | None]]]


def _error(exc: BaseException) -> dict[str, Any]:
    message = (str(exc).strip() or type(exc).__name__)[:500]
    return {
        "code": getattr(exc, "code", "RUNTIME"),
        "message": mask_phones_in_text(message),
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


def _defer_for_cooldown(
    conn,
    job: dict[str, Any],
    retry_after: int,
    *,
    wall_clock,
) -> tuple[dict[str, Any], str]:
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
    return final, "cooldown_deferred"


def _finish_from_result(
    conn,
    job: dict[str, Any],
    result: dict,
    *,
    wall_clock,
    requeue_reason: str | None = None,
) -> dict[str, Any]:
    if store.cancel_requested(conn, job):
        return store.finish_cancelled(conn, job, result, now=wall_clock())
    if bool(result["remaining"]):
        reason = (
            requeue_reason
            if requeue_reason is not None
            else str(result.get("stop_reason") or "remaining")
        )
        return store.requeue(
            conn,
            job,
            result=result,
            reason=reason,
            now=wall_clock(),
        )
    return store.complete(conn, job, result, now=wall_clock())


def _record_outcome(
    counts: dict[str, int],
    outcomes: list[dict[str, Any]],
    final: dict[str, Any],
) -> None:
    counts[final["state"]] += 1
    outcomes.append(_outcome(final))
    _notify_failed(final)


def _run_lane_loop(
    alias: str,
    lane: str,
    *,
    max_runtime: float,
    monotonic=time.monotonic,
    wall_clock=lambda: datetime.now(UTC),
    on_open: Callable[[Any], dict[str, Any] | None],
    process_job: ProcessJob,
    account_fields: Callable[[dict[str, Any]], dict[str, Any]],
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
    conn = store.connect_mutating(alias)
    context: dict[str, Any] = {}
    try:
        opened = on_open(conn)
        context = opened or {}
        recovered = store.recover_running(conn, lane, now=wall_clock())
        while monotonic() - started < max_runtime:
            job = store.claim_next(conn, lane, now=wall_clock())
            if job is None:
                break
            counts["selected"] += 1
            final, loop_stop = process_job(
                conn,
                job,
                context=context,
                started=started,
                max_runtime=max_runtime,
                monotonic=monotonic,
                wall_clock=wall_clock,
            )
            _record_outcome(counts, outcomes, final)
            if loop_stop is not None:
                stop_reason = loop_stop
                break
        else:
            stop_reason = "wall_clock_cap"
    finally:
        conn.close()
    return {
        "account": {"alias": alias, **account_fields(context)},
        "lane": lane,
        **counts,
        "recovered": recovered,
        "outcomes": outcomes,
        "stop_reason": stop_reason,
    }


async def _run_lane_loop_async(
    alias: str,
    lane: str,
    *,
    max_runtime: float,
    monotonic=time.monotonic,
    wall_clock=lambda: datetime.now(UTC),
    on_open: Callable[[Any], Awaitable[dict[str, Any] | None]],
    process_job: AsyncProcessJob,
    account_fields: Callable[[dict[str, Any]], dict[str, Any]],
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
    conn = store.connect_mutating(alias)
    context: dict[str, Any] = {}
    try:
        opened = await on_open(conn)
        context = opened or {}
        recovered = store.recover_running(conn, lane, now=wall_clock())
        while monotonic() - started < max_runtime:
            job = store.claim_next(conn, lane, now=wall_clock())
            if job is None:
                break
            counts["selected"] += 1
            final, loop_stop = await process_job(
                conn,
                job,
                context=context,
                started=started,
                max_runtime=max_runtime,
                monotonic=monotonic,
                wall_clock=wall_clock,
            )
            _record_outcome(counts, outcomes, final)
            if loop_stop is not None:
                stop_reason = loop_stop
                break
        else:
            stop_reason = "wall_clock_cap"
    finally:
        conn.close()
    return {
        "account": {"alias": alias, **account_fields(context)},
        "lane": lane,
        **counts,
        "recovered": recovered,
        "outcomes": outcomes,
        "stop_reason": stop_reason,
    }


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
    source_kind: str | None,
) -> dict:
    kind = model.require_kind(job["kind"])
    if kind.name in ("archive-backfill", "archive-sync"):
        return archive_jobs_cmd.progress_token(alias, config)
    if kind.name == "clone-sync":
        if source_peer_id is None or source_kind is None:
            raise PolicyError("clone progress requires a resolved source identity")
        return clone_cmd.progress_token(user_id, source_peer_id, source_kind)
    raise PolicyError(f"unsupported telegram job kind: {job['kind']}")


async def _run_telegram_job(
    tg,
    job: dict[str, Any],
    alias: str,
    config: Config | None,
    *,
    should_stop,
) -> dict:
    kind = model.require_kind(job["kind"])
    if kind.lane != "telegram":
        raise PolicyError(f"unsupported telegram job kind: {job['kind']}")
    spec = job["spec"]
    if kind.name == "archive-backfill":
        return await archive_jobs_cmd.backfill_quantum(
            tg,
            alias,
            chats=list(spec["chats"]),
            private=bool(spec["private"]),
            limit=int(spec["limit"]),
            config=config,
            should_stop=should_stop,
        )
    if kind.name == "archive-sync":
        return await archive_cmd.sync(
            tg,
            alias,
            max_events=int(spec["max_events"]),
            max_dialogs=int(spec["max_dialogs"]),
            max_media=int(spec["max_media"]),
            config=config,
            should_stop=should_stop,
        )
    if kind.name == "clone-sync":
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
    def process_job(
        conn,
        job: dict[str, Any],
        *,
        context,
        started,
        max_runtime,
        monotonic,
        wall_clock,
    ) -> tuple[dict[str, Any], str | None]:
        try:
            kind = model.require_kind(job["kind"])
            if kind.lane != "local":
                raise PolicyError(f"unsupported local job kind: {job['kind']}")
            result = _run_transcription(job, alias, context["config"])
            final = _finish_from_result(
                conn,
                job,
                result,
                wall_clock=wall_clock,
                requeue_reason="remaining",
            )
        except (PolicyError, ConfigError, NotFoundError) as exc:
            final = store.fail_terminal(conn, job, _error(exc), now=wall_clock())
        except Exception as exc:
            final = store.fail_runtime(conn, job, _error(exc), now=wall_clock())
        return final, None

    return _run_lane_loop(
        alias,
        "local",
        max_runtime=max_runtime,
        monotonic=monotonic,
        wall_clock=wall_clock,
        on_open=lambda _conn: {"config": config},
        process_job=process_job,
        account_fields=lambda _context: {},
    )


async def run_telegram(
    tg,
    alias: str,
    *,
    max_runtime: float,
    config: Config | None,
    monotonic=time.monotonic,
    wall_clock=lambda: datetime.now(UTC),
) -> dict[str, Any]:
    async def on_open(conn):
        me = await tg.get_me()
        user_id = int(me.id)
        store.bind_user(conn, user_id)
        return {"user_id": user_id}

    async def process_job(
        conn,
        job: dict[str, Any],
        *,
        context,
        started,
        max_runtime,
        monotonic,
        wall_clock,
    ) -> tuple[dict[str, Any], str | None]:
        user_id = int(context["user_id"])
        before: dict | None = None
        source_peer_id: int | None = None
        source_kind: str | None = None
        halt_after = False
        loop_stop: str | None = None
        try:
            kind = model.require_kind(job["kind"])
            if kind.name == "clone-sync":
                (
                    source_peer_id,
                    source_kind,
                ) = await clone_cmd.resolve_source_identity(
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
                source_kind,
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
            final = _finish_from_result(conn, job, result, wall_clock=wall_clock)
        except PartialFailure as exc:
            cause = exc.cause
            if isinstance(cause, (PolicyError, ConfigError, NotFoundError)):
                final = store.fail_terminal(conn, job, _error(cause), now=wall_clock())
            else:
                final = store.fail_runtime(conn, job, _error(cause), now=wall_clock())
        except RateLimitError as exc:
            retry_after = int(exc.details.get("retry_after") or 0)
            final, loop_stop = _defer_for_cooldown(
                conn, job, retry_after, wall_clock=wall_clock
            )
        except telethon_errors.FloodWaitError as exc:
            retry_after = int(exc.seconds)
            final, loop_stop = _defer_for_cooldown(
                conn, job, retry_after, wall_clock=wall_clock
            )
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
                    source_kind,
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
        result_stop = (final.get("last_result") or {}).get("stop_reason")
        if result_stop is not None or loop_stop is not None or halt_after:
            loop_stop = str(result_stop or loop_stop or "idle")
        return final, loop_stop

    return await _run_lane_loop_async(
        alias,
        "telegram",
        max_runtime=max_runtime,
        monotonic=monotonic,
        wall_clock=wall_clock,
        on_open=on_open,
        process_job=process_job,
        account_fields=lambda context: {"user_id": context["user_id"]},
    )
