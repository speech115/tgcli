"""Lean faithful-mirror command facade (ADR-0014)."""

from telethon.tl import functions, types

from tgcli import chatref, safety
from tgcli.errors import NotFoundError, PolicyError
from tgcli.mirror.store import CopyOperation, MirrorRecord, MirrorStore


def _temporary_title(record: MirrorRecord) -> str:
    return f"[tgcli:{record.mirror_id[:12]}]"


def _is_private_owned_broadcast(entity, *, title: str | None = None) -> bool:
    return bool(
        (title is None or getattr(entity, "title", None) == title)
        and getattr(entity, "creator", False)
        and getattr(entity, "broadcast", False)
        and not getattr(entity, "megagroup", False)
        and getattr(entity, "username", None) is None
    )


async def _resolve(tg, source: str) -> tuple[object, object]:
    try:
        entity = await tg.get_entity(chatref.parse(source))
    except ValueError:
        raise NotFoundError(f"channel not found: {source!r}") from None
    if not getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
        raise PolicyError("mirror source must be a broadcast channel")
    me = await tg.get_me()
    return entity, me


def _result(record: MirrorRecord) -> dict:
    destination = None
    if record.destination_peer_id is not None:
        destination = {
            "id": record.destination_peer_id,
            "title": record.source_title,
        }
    return {
        "mirror": {
            "id": record.mirror_id,
            "source": {"id": record.source_peer_id, "title": record.source_title},
            "destination": destination,
            "status": "authorized" if record.authorized else "planned",
            "commit_required": not record.authorized,
        }
    }


async def preview_init(tg, source: str, account_alias: str) -> dict:
    del account_alias
    entity, me = await _resolve(tg, source)
    record = MirrorStore().create(me.id, entity.id, entity.title)
    return _result(record)


async def _find_marker_matches(tg, marker: str) -> list[object]:
    matches = []
    async for dialog in tg.iter_dialogs():
        entity = getattr(dialog, "entity", None)
        if _is_private_owned_broadcast(entity, title=marker):
            matches.append(entity)
    return matches


async def _destination_entity(tg, record: MirrorRecord):
    try:
        entity = await tg.get_entity(types.PeerChannel(record.destination_peer_id))
    except ValueError:
        raise PolicyError("authorized mirror destination is unavailable") from None
    if not _is_private_owned_broadcast(entity):
        raise PolicyError("mirror destination is not a private owned broadcast channel")
    return entity


async def commit_init(tg, source: str, account_alias: str) -> dict:
    entity, me = await _resolve(tg, source)
    store = MirrorStore()
    record = store.create(me.id, entity.id, entity.title)

    if record.authorized:
        destination = await _destination_entity(tg, record)
    else:
        marker = _temporary_title(record)
        matches = await _find_marker_matches(tg, marker)
        if len(matches) > 1:
            raise PolicyError("mirror destination marker matched multiple channels")
        if matches:
            destination = matches[0]
        else:
            safety.append_audit(
                "mirror-init-create",
                account_alias,
                {"mirror_id": record.mirror_id, "phase": "attempt"},
            )
            update = await tg(
                functions.channels.CreateChannelRequest(
                    title=marker,
                    about="",
                    broadcast=True,
                    megagroup=False,
                )
            )
            candidates = [
                candidate
                for candidate in getattr(update, "chats", ())
                if _is_private_owned_broadcast(candidate, title=marker)
            ]
            if len(candidates) != 1:
                raise PolicyError("Telegram did not return the created private channel")
            destination = candidates[0]
        record = store.authorize(destination.id)

    if getattr(destination, "title", None) != record.source_title:
        safety.append_audit(
            "mirror-init-title",
            account_alias,
            {"mirror_id": record.mirror_id, "phase": "attempt"},
        )
        await tg(
            functions.channels.EditTitleRequest(
                channel=destination,
                title=record.source_title,
            )
        )
    return _result(record)


def _destination_message_id(response, random_id: int) -> int:
    matches = [
        update.id
        for update in getattr(response, "updates", ())
        if isinstance(update, types.UpdateMessageID)
        and update.random_id == random_id
    ]
    if len(matches) != 1:
        raise PolicyError("Telegram did not confirm the mirrored message")
    return matches[0]


def _require_text_message(message) -> None:
    if getattr(message, "media", None) is not None or getattr(
        message, "action", None
    ) is not None:
        raise PolicyError(
            f"mirror sync supports text messages only; stopped at source message {message.id}"
        )


async def _forward_copy(
    tg,
    *,
    source_peer,
    destination_peer,
    operation: CopyOperation,
    account_alias: str,
    mirror_id: str,
) -> int:
    safety.append_audit(
        "mirror-sync-forward",
        account_alias,
        {
            "mirror_id": mirror_id,
            "source_message_id": operation.source_message_id,
            "random_id": operation.random_id,
        },
    )
    response = await tg(
        functions.messages.ForwardMessagesRequest(
            from_peer=source_peer,
            id=[operation.source_message_id],
            random_id=[operation.random_id],
            to_peer=destination_peer,
            drop_author=True,
        )
    )
    return _destination_message_id(response, operation.random_id)


async def sync_text(tg, source: str, account_alias: str) -> dict:
    entity, me = await _resolve(tg, source)
    store = MirrorStore()
    record = store.create(me.id, entity.id, entity.title)
    if not record.authorized:
        raise PolicyError("mirror source is not authorized; run mirror init --commit")
    if getattr(entity, "noforwards", False):
        raise PolicyError("protected mirror sources require the later media sync slice")

    destination = await _destination_entity(tg, record)
    source_peer = await tg.get_input_entity(entity)
    destination_peer = await tg.get_input_entity(destination)
    copied = 0
    for operation in store.pending_copies():
        message = await tg.get_messages(entity, ids=operation.source_message_id)
        if message is None:
            raise PolicyError(
                f"pending source message is unavailable: {operation.source_message_id}"
            )
        _require_text_message(message)
        destination_message_id = await _forward_copy(
            tg,
            source_peer=source_peer,
            destination_peer=destination_peer,
            operation=operation,
            account_alias=account_alias,
            mirror_id=record.mirror_id,
        )
        store.confirm_copy(operation.source_message_id, destination_message_id)
        copied += 1

    cursor = store.last_confirmed_message_id()
    async for message in tg.iter_messages(entity, min_id=cursor, reverse=True):
        _require_text_message(message)
        operation = store.prepare_copy(message.id)
        destination_message_id = await _forward_copy(
            tg,
            source_peer=source_peer,
            destination_peer=destination_peer,
            operation=operation,
            account_alias=account_alias,
            mirror_id=record.mirror_id,
        )
        store.confirm_copy(message.id, destination_message_id)
        copied += 1

    return {
        "mirror": _result(record)["mirror"],
        "sync": {
            "copied": copied,
            "last_confirmed_message_id": store.last_confirmed_message_id(),
        },
    }


def to_rows(data: dict) -> list[tuple]:
    mirror = data["mirror"]
    destination = mirror["destination"] or {}
    if "sync" in data:
        return [
            (
                data["sync"]["copied"],
                data["sync"]["last_confirmed_message_id"],
                mirror["id"],
                mirror["source"]["id"],
                destination.get("id"),
            )
        ]
    return [
        (
            mirror["status"],
            mirror["id"],
            mirror["source"]["id"],
            destination.get("id"),
        )
    ]
