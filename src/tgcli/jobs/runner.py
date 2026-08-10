"""Foreground lane runner for typed jobs (ADR-0087)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from tgcli.commands import archive as archive_cmd
from tgcli.config import Config
from tgcli.errors import ConfigError, NotFoundError, PolicyError
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


def _run_transcription(job: dict[str, Any], alias: str, config: Config) -> dict:
    return archive_cmd.transcribe(
        alias,
        limit=1,
        max_attempts=int(job["spec"]["max_attempts"]),
        config=config,
    )


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
