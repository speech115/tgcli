"""Read-only JSONL batch runner (ADR-0032)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from telethon import errors as telethon_errors

from tgcli.commands import dialogs as dialogs_cmd
from tgcli.commands import identity as identity_cmd
from tgcli.commands import info as info_cmd
from tgcli.commands import media as media_cmd
from tgcli.commands import read as read_cmd
from tgcli.commands import search as search_cmd
from tgcli.commands import thread as thread_cmd
from tgcli.errors import PolicyError, RateLimitError, TgcliError

BATCH_OP_CAP = 100

ALLOWED_OPS = frozenset(
    {
        "dialogs",
        "read",
        "search",
        "latest",
        "message",
        "info",
        "count",
        "resolve",
        "mutual-chats",
        "contacts.list",
        "contacts.search",
        "media.manifest",
        "thread",
    }
)


def _parse_when(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PolicyError(f"batch {field} must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PolicyError(f"batch {field} must be an ISO 8601 string") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_ops(raw_lines: list[str]) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    for index, line in enumerate(raw_lines, start=1):
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PolicyError(f"batch line {index}: invalid JSON") from exc
        if not isinstance(payload, dict):
            raise PolicyError(f"batch line {index}: op must be a JSON object")
        op = payload.get("op")
        if not isinstance(op, str) or not op:
            raise PolicyError(f"batch line {index}: missing op")
        if op not in ALLOWED_OPS:
            raise PolicyError(f"batch line {index}: op {op!r} is not allowlisted")
        ops.append(payload)
    if len(ops) > BATCH_OP_CAP:
        raise PolicyError(f"tg batch accepts at most {BATCH_OP_CAP} ops per invocation")
    return ops


async def _dispatch(tg, payload: dict[str, Any]) -> dict[str, Any]:
    op = payload["op"]
    if op == "dialogs":
        return await dialogs_cmd.fetch_dialogs(
            tg,
            limit=int(payload.get("limit", 50)),
            unread_only=bool(payload.get("unread_only", False)),
            kind=payload.get("kind"),
        )
    if op == "read":
        return await read_cmd.fetch_messages(
            tg,
            payload["chat"],
            limit=int(payload.get("limit", 20)),
            after_id=payload.get("after_id"),
            before_id=payload.get("before_id"),
            since=_parse_when(payload.get("since"), "read.since"),
            until=_parse_when(payload.get("until"), "read.until"),
            topic=payload.get("topic"),
        )
    if op == "search":
        if payload.get("all"):
            return await search_cmd.fetch_search_all(
                tg, payload["query"], limit=int(payload.get("limit", 20))
            )
        return await search_cmd.fetch_search(
            tg,
            payload["chat"],
            payload["query"],
            limit=int(payload.get("limit", 20)),
            from_user=payload.get("from"),
            since=_parse_when(payload.get("since"), "search.since"),
        )
    if op == "latest":
        return await search_cmd.fetch_latest(tg, payload["chat"])
    if op == "message":
        return await read_cmd.fetch_message(
            tg,
            payload["chat"],
            int(payload["message_id"]),
            context=int(payload.get("context", 0)),
        )
    if op == "info":
        if payload.get("full"):
            return await info_cmd.fetch_info_full(tg, payload["chat"])
        return await info_cmd.fetch_info(tg, payload["chat"])
    if op == "count":
        return await info_cmd.fetch_count(tg, payload["chat"])
    if op == "resolve":
        return await identity_cmd.resolve(tg, payload["ref"])
    if op == "mutual-chats":
        return await identity_cmd.mutual_chats(tg, payload["ref"])
    if op == "contacts.list":
        return await identity_cmd.contacts_list(tg)
    if op == "contacts.search":
        return await identity_cmd.contacts_search(
            tg, payload["query"], use_global=bool(payload.get("global", False))
        )
    if op == "media.manifest":
        return await media_cmd.manifest(
            tg,
            payload["source"],
            kind=payload.get("type"),
            since=_parse_when(payload.get("since"), "media.manifest.since"),
            limit=int(payload.get("limit", 100)),
        )
    if op == "thread":
        return await thread_cmd.fetch_thread(
            tg,
            payload["chat"],
            int(payload["message_id"]),
            depth=int(payload.get("depth", 20)),
            want_replies=bool(payload.get("replies", False)),
            replies_limit=int(payload.get("limit", 50)),
        )
    raise PolicyError(f"unhandled batch op: {op!r}")


async def run_batch(
    tg, ops: list[dict[str, Any]], *, fail_fast: bool = False
) -> tuple[list[dict[str, Any]], int | None]:
    """Execute ops sequentially. Returns (result lines, first failure exit code)."""
    results: list[dict[str, Any]] = []
    first_exit: int | None = None
    for payload in ops:
        op = payload["op"]
        try:
            data = await _dispatch(tg, payload)
            results.append({"ok": True, "op": op, "data": data})
        except TgcliError as exc:
            results.append(
                {
                    "ok": False,
                    "op": op,
                    "error": {"code": exc.code, "message": str(exc), **exc.details},
                }
            )
            if first_exit is None:
                first_exit = exc.exit_code
            if fail_fast:
                break
        except telethon_errors.FloodWaitError as exc:
            rate = RateLimitError(
                f"rate limited for {exc.seconds}s", retry_after=exc.seconds
            )
            results.append(
                {
                    "ok": False,
                    "op": op,
                    "error": {
                        "code": rate.code,
                        "message": str(rate),
                        **rate.details,
                    },
                }
            )
            if first_exit is None:
                first_exit = rate.exit_code
            if fail_fast:
                break
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "op": op,
                    "error": {"code": "RUNTIME", "message": str(exc)},
                }
            )
            if first_exit is None:
                first_exit = TgcliError.exit_code
            if fail_fast:
                break
    return results, first_exit
