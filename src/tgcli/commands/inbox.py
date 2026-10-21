"""`tg inbox`: what is new since the last acknowledged check.

`read` walks acked and unread dialogs and returns, per dialog, the messages
after its checkpoint, oldest first. It never moves a checkpoint: it returns an
`ack` token, and `ack TOKEN` advances the checkpoints only once the caller has
processed what it read. A dialog never acked starts after Telegram's own read
marker. Message ids, not the unread counter, are the durable boundary.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from tgcli import session
from tgcli.atomic import replace_text
from tgcli.commands.dialogs import _kind
from tgcli.commands.read import message_to_dict, sanitize_plain_text
from tgcli.errors import PolicyError

TOKEN_PREFIX = "v1:"


def _state_path(alias: str) -> Path:
    return session.state_dir() / "inbox" / f"{alias}.json"


def load_checkpoints(alias: str) -> dict[int, int]:
    path = _state_path(alias)
    try:
        raw = json.loads(path.read_text())
        return {int(peer): int(last) for peer, last in raw["dialogs"].items()}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise PolicyError(f"inbox state {path} is unreadable: {exc!r}") from exc


def _save_checkpoints(alias: str, checkpoints: dict[int, int]) -> None:
    session.ensure_state_dir("inbox")
    body = {
        "version": 1,
        "dialogs": {str(p): i for p, i in sorted(checkpoints.items())},
    }
    replace_text(_state_path(alias), json.dumps(body, indent=2) + "\n")


def encode_token(alias: str, newest: dict[int, int]) -> str:
    # The account is in the token: message ids in private chats are per account,
    # so another account's ids would push its checkpoints past unseen messages.
    dialogs = {str(p): i for p, i in sorted(newest.items())}
    raw = json.dumps({"account": alias, "dialogs": dialogs}).encode()
    return TOKEN_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_token(alias: str, text: str) -> dict[int, int]:
    if not text.startswith(TOKEN_PREFIX):
        raise PolicyError("inbox ack token is missing or unsupported")
    body = text[len(TOKEN_PREFIX) :]
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        owner = payload["account"]
        newest = {int(peer): last for peer, last in payload["dialogs"].items()}
    except (ValueError, AttributeError, TypeError, KeyError, UnicodeDecodeError) as exc:
        raise PolicyError("inbox ack token is corrupt") from exc
    for last in newest.values():
        if isinstance(last, bool) or not isinstance(last, int) or last < 0:
            raise PolicyError("inbox ack token is corrupt")
    if owner != alias:
        raise PolicyError(
            f"inbox ack token belongs to account {owner!r}, not {alias!r}"
        )
    return newest


async def fetch_inbox(tg, alias: str, *, limit: int) -> dict:
    checkpoints = load_checkpoints(alias)
    dialogs = []
    newest: dict[int, int] = {}
    async for dialog in tg.iter_dialogs():
        after_id = checkpoints.get(dialog.id)
        if after_id is None:
            # Never acked: only unread dialogs, from Telegram's read marker.
            if not dialog.unread_count:
                continue
            after_id = dialog.dialog.read_inbox_max_id
        top = dialog.message
        if top is not None and top.id <= after_id:
            continue  # the dialog list already shows nothing newer: no history read
        # reverse=True walks up from after_id, so a capped page is the oldest
        # one and acking its newest id skips nothing.
        messages = [
            message_to_dict(message, dialog.entity)
            async for message in tg.iter_messages(
                dialog.entity, min_id=after_id, limit=limit + 1, reverse=True
            )
        ]
        more = len(messages) > limit
        messages = messages[:limit]
        if not messages:
            continue
        newest[dialog.id] = messages[-1]["id"]
        dialogs.append(
            {
                "id": dialog.id,
                "name": dialog.name,
                "kind": _kind(dialog),
                "after_id": after_id,
                "messages": messages,
                "more": more,
            }
        )
    return {
        "dialogs": dialogs,
        "more": any(dialog["more"] for dialog in dialogs),
        "ack": encode_token(alias, newest) if newest else None,
    }


def ack(alias: str, token: str) -> dict:
    newest = decode_token(alias, token)
    checkpoints = load_checkpoints(alias)
    # max(): acking an older token, or the same one twice, never moves back.
    for peer, last in newest.items():
        checkpoints[peer] = max(checkpoints.get(peer, 0), last)
    _save_checkpoints(alias, checkpoints)
    return {
        "acked": [
            {"id": peer, "checkpoint": checkpoints[peer]} for peer in sorted(newest)
        ]
    }


def to_rows(data: dict) -> list[tuple]:
    return [
        (
            dialog["id"],
            message["id"],
            message["date"],
            sanitize_plain_text(message["from"]["name"]),
            sanitize_plain_text(message["text"]),
        )
        for dialog in data["dialogs"]
        for message in dialog["messages"]
    ]


def ack_rows(data: dict) -> list[tuple]:
    return [(item["id"], item["checkpoint"]) for item in data["acked"]]
