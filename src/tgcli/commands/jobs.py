"""`tg jobs` local control plane and foreground runners (ADR-0087)."""

from __future__ import annotations

from tgcli.config import Config, resolve_account
from tgcli.errors import NotFoundError
from tgcli.jobs import model, runner, store


def resolve_alias(requested: str | None, config: Config) -> str:
    return resolve_account(config, requested).alias


def add(
    alias: str,
    *,
    key: str,
    kind: str,
    lane: str,
    spec: dict,
    priority: str | None,
    replace: bool,
) -> dict:
    conn = store.connect(alias)
    try:
        return store.add_job(
            conn,
            key=key,
            kind=kind,
            lane=lane,
            spec=spec,
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
    conn = store.connect_mutating(alias)
    try:
        data = store.cancel_job(conn, key)
    finally:
        conn.close()
    data["account"] = {"alias": alias}
    return data


def rearm(alias: str, key: str, *, expected_lane: str) -> dict:
    conn = store.connect_mutating(alias)
    try:
        data = store.rearm_job(conn, key, expected_lane=expected_lane)
    finally:
        conn.close()
    data["account"] = {"alias": alias}
    return data


def run_local(alias: str, *, max_runtime: float, config: Config) -> dict:
    return runner.run_local(alias, max_runtime=max_runtime, config=config)


async def run_telegram(
    tg, alias: str, *, max_runtime: float, config: Config | None
) -> dict:
    return await runner.run_telegram(tg, alias, max_runtime=max_runtime, config=config)


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


def execute_offline(args, config: Config) -> tuple[dict, list[tuple]] | None:
    """Run control-plane/local commands; Telegram lane returns to dispatch."""
    alias = resolve_alias(args.account, config)
    args.account = alias
    if args.jobs_command == "add":
        data = add(
            alias,
            key=args.key,
            kind=args.job_kind,
            lane=args.job_lane,
            spec=args.job_spec,
            priority=args.priority,
            replace=bool(args.replace),
        )
        return data, job_rows(data)
    if args.jobs_command == "list":
        data = list_jobs(alias)
        return data, list_rows(data)
    if args.jobs_command == "show":
        data = show(alias, args.key)
        return data, job_rows(data)
    if args.jobs_command == "cancel":
        data = cancel(alias, args.key)
        return data, job_rows(data)
    if args.lane == "local":
        data = run_local(alias, max_runtime=args.max_runtime, config=config)
        return data, run_rows(data)
    return None
