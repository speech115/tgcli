"""`tg jobs` local control plane and foreground runners (ADR-0087)."""

from __future__ import annotations

from tgcli.config import Config, resolve_account
from tgcli.errors import NotFoundError
from tgcli.jobs import model, runner, store


def resolve_alias(requested: str | None, config: Config) -> str:
    return resolve_account(config, requested).alias


def add_transcribe(
    alias: str,
    *,
    key: str,
    max_attempts: int | None,
    priority: str | None,
    replace: bool,
) -> dict:
    conn = store.connect(alias)
    try:
        return store.add_job(
            conn,
            key=key,
            kind="archive-transcribe",
            lane="local",
            spec=model.transcribe_spec(max_attempts),
            priority=model.validate_priority(priority),
            replace=replace,
        )
    finally:
        conn.close()


def list_jobs(alias: str) -> dict:
    path = store.path_for(alias)
    if not path.is_file():
        return {"account": {"alias": alias}, "jobs": []}
    conn = store.connect_existing(alias)
    try:
        return {"account": {"alias": alias}, "jobs": store.list_jobs(conn)}
    finally:
        conn.close()


def show(alias: str, key: str) -> dict:
    try:
        conn = store.connect_existing(alias)
    except NotFoundError as exc:
        raise NotFoundError(f"unknown job key: {key!r}") from exc
    try:
        data = store.show_job(conn, key)
    finally:
        conn.close()
    data["account"] = {"alias": alias}
    return data


def cancel(alias: str, key: str) -> dict:
    conn = store.connect_existing(alias)
    try:
        data = store.cancel_job(conn, key)
    finally:
        conn.close()
    data["account"] = {"alias": alias}
    return data


def run(alias: str, *, lane: str, max_runtime: float, config: Config) -> dict:
    if lane != "local":
        raise AssertionError(f"unhandled jobs lane: {lane}")
    return runner.run_local(alias, max_runtime=max_runtime, config=config)


def job_rows(data: dict) -> list[tuple]:
    job = data["job"]
    return [
        (
            job["key"],
            job["generation"],
            job["kind"],
            job["lane"],
            job["priority"],
            job["state"],
        )
    ]


def list_rows(data: dict) -> list[tuple]:
    return [
        (
            job["key"],
            job["generation"],
            job["kind"],
            job["lane"],
            job["priority"],
            job["state"],
            job["not_before"],
        )
        for job in data["jobs"]
    ]


def run_rows(data: dict) -> list[tuple]:
    return [
        (
            data["lane"],
            data["selected"],
            data["completed"],
            data["queued"],
            data["failed"],
            data["cancelled"],
            data["stop_reason"],
        )
    ]
