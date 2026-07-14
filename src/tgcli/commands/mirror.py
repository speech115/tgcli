"""Lean faithful-mirror command facade (ADR-0014)."""

from datetime import datetime, timezone
from math import ceil

from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tgcli import chatref, safety
from tgcli.errors import NotFoundError, PolicyError, RateLimitError
from tgcli.mirror.store import (
    CopyOperation,
    MirrorRecord,
    MirrorStore,
    account_mutation_lock,
    cooldown_deadline,
    record_cooldown,
)


def _temporary_title(record: MirrorRecord) -> str:
    return f"[tgcli:{record.mirror_id[:12]}]"


def _is_private_owned_broadcast(entity, *, title: str | None = None) -> bool:
    active_usernames = any(
        getattr(item, "active", False)
        for item in (getattr(entity, "usernames", None) or ())
    )
    return bool(
        (title is None or getattr(entity, "title", None) == title)
        and getattr(entity, "creator", False)
        and getattr(entity, "broadcast", False)
        and not getattr(entity, "megagroup", False)
        and getattr(entity, "username", None) is None
        and not active_usernames
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


def _enforce_cooldown(account_user_id: int) -> None:
    deadline = cooldown_deadline(account_user_id)
    if deadline is None:
        return
    retry_after = ceil((deadline - datetime.now(timezone.utc)).total_seconds())
    if retry_after > 0:
        raise RateLimitError(
            f"rate limited for {retry_after}s", retry_after=retry_after
        )


async def _dispatch_mutation(tg, request, account_user_id: int):
    try:
        return await tg(request)
    except telethon_errors.FloodWaitError as exc:
        record_cooldown(account_user_id, exc.seconds)
        raise


def _result(record: MirrorRecord, *, destination_title: str | None = None) -> dict:
    destination = None
    if record.destination_peer_id is not None:
        destination = {
            "id": record.destination_peer_id,
            "title": destination_title,
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
    if not record.authorized:
        return _result(record)
    destination = await _destination_entity(tg, record)
    return _result(record, destination_title=getattr(destination, "title", None))


async def _find_marker_candidates(
    tg, marker: str
) -> tuple[list[object], list[object]]:
    valid = []
    wrong_shape = []
    async for dialog in tg.iter_dialogs():
        entity = getattr(dialog, "entity", None)
        if getattr(entity, "title", None) != marker:
            continue
        if _is_private_owned_broadcast(entity, title=marker):
            valid.append(entity)
        else:
            wrong_shape.append(entity)
    return valid, wrong_shape


async def _destination_entity(tg, record: MirrorRecord):
    try:
        entity = await tg.get_entity(types.PeerChannel(record.destination_peer_id))
    except ValueError:
        raise PolicyError("authorized mirror destination is unavailable") from None
    if not _is_private_owned_broadcast(entity):
        raise PolicyError("mirror destination is not a private owned broadcast channel")
    return entity


async def commit_init(
    tg,
    source: str,
    account_alias: str,
    *,
    retry_create: bool = False,
    confirm: str | None = None,
) -> dict:
    entity, me = await _resolve(tg, source)
    with account_mutation_lock(me.id):
        _enforce_cooldown(me.id)
        store = MirrorStore()
        record = store.create(me.id, entity.id, entity.title)
        if retry_create != (confirm is not None) or (
            confirm is not None and confirm != record.mirror_id
        ):
            raise PolicyError("retry create requires --confirm with the exact mirror id")

        if record.authorized:
            if retry_create:
                raise PolicyError(
                    "retry create flags are not applicable to an authorized mirror"
                )
            destination = await _destination_entity(tg, record)
        else:
            marker = record.creation_marker or _temporary_title(record)
            matches, wrong_shape = await _find_marker_candidates(tg, marker)
            if len(matches) + len(wrong_shape) > 1:
                store.mark_create_blocked()
                raise PolicyError("mirror destination marker matched multiple channels")
            if wrong_shape:
                store.mark_create_blocked()
                raise PolicyError(
                    "mirror destination marker matched a channel with wrong shape"
                )
            if matches:
                destination = matches[0]
            else:
                if record.creation_state != "planned" and not retry_create:
                    raise PolicyError(
                        "ambiguous mirror creation requires explicit retry with the exact mirror id"
                    )
                store.mark_create_dispatched(marker, datetime.now(timezone.utc))
                safety.append_audit(
                    "mirror-init-create",
                    account_alias,
                    {"mirror_id": record.mirror_id, "phase": "attempt"},
                )
                update = await _dispatch_mutation(
                    tg,
                    functions.channels.CreateChannelRequest(
                        title=marker,
                        about="",
                        broadcast=True,
                        megagroup=False,
                    ),
                    me.id,
                )
                candidates = [
                    candidate
                    for candidate in getattr(update, "chats", ())
                    if _is_private_owned_broadcast(candidate, title=marker)
                ]
                if len(candidates) != 1:
                    raise PolicyError(
                        "Telegram did not return the created private channel"
                    )
                destination = candidates[0]
            record = store.authorize(destination.id)

        destination_title = getattr(destination, "title", None)
        if destination_title != record.source_title:
            safety.append_audit(
                "mirror-init-title",
                account_alias,
                {"mirror_id": record.mirror_id, "phase": "attempt"},
            )
            await _dispatch_mutation(
                tg,
                functions.channels.EditTitleRequest(
                    channel=destination,
                    title=record.source_title,
                ),
                me.id,
            )
            destination_title = record.source_title
        return _result(record, destination_title=destination_title)


def _destination_message_id(response, random_id: int) -> int:
    updates = getattr(response, "updates", None)
    if updates is None:
        update = getattr(response, "update", None)
        updates = () if update is None else (update,)
    matches = [
        update.id
        for update in updates
        if isinstance(update, types.UpdateMessageID)
        and update.random_id == random_id
    ]
    if len(matches) != 1:
        raise PolicyError("Telegram did not confirm the mirrored message")
    return matches[0]


_NATIVE_MEDIA_TYPES = (
    types.MessageMediaWebPage,
    types.MessageMediaPhoto,
    types.MessageMediaDocument,
)


def _require_supported_message(message) -> None:
    if getattr(message, "noforwards", False):
        raise PolicyError(
            f"protected mirror messages require the later reconstruction slice; "
            f"stopped at source message {message.id}"
        )
    if getattr(message, "grouped_id", None) is not None:
        raise PolicyError(
            f"mirror albums require the later atomic album slice; "
            f"stopped at source message {message.id}"
        )
    if getattr(message, "reply_to", None) is not None:
        raise PolicyError(
            f"mirror replies require the later mapped reply slice; "
            f"stopped at source message {message.id}"
        )
    if getattr(message, "action", None) is not None:
        raise PolicyError(
            f"mirror service messages are not supported; "
            f"stopped at source message {message.id}"
        )
    media = getattr(message, "media", None)
    if media is not None and not isinstance(media, _NATIVE_MEDIA_TYPES):
        raise PolicyError(
            f"mirror media type {type(media).__name__} is not supported; "
            f"stopped at source message {message.id}"
        )


async def _forward_copy(
    tg,
    *,
    source_peer,
    destination_peer,
    operation: CopyOperation,
    account_user_id: int,
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
    response = await _dispatch_mutation(
        tg,
        functions.messages.ForwardMessagesRequest(
            from_peer=source_peer,
            id=[operation.source_message_id],
            random_id=[operation.random_id],
            to_peer=destination_peer,
            drop_author=True,
        ),
        account_user_id,
    )
    return _destination_message_id(response, operation.random_id)


async def sync_text(tg, source: str, account_alias: str) -> dict:
    entity, me = await _resolve(tg, source)
    with account_mutation_lock(me.id):
        _enforce_cooldown(me.id)
        store = MirrorStore()
        record = store.create(me.id, entity.id, entity.title)
        if not record.authorized:
            raise PolicyError("mirror source is not authorized; run mirror init --commit")
        if getattr(entity, "noforwards", False):
            raise PolicyError(
                "protected mirror sources require the later media sync slice"
            )

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
            _require_supported_message(message)
            destination_message_id = await _forward_copy(
                tg,
                source_peer=source_peer,
                destination_peer=destination_peer,
                operation=operation,
                account_user_id=me.id,
                account_alias=account_alias,
                mirror_id=record.mirror_id,
            )
            store.confirm_copy(operation.source_message_id, destination_message_id)
            copied += 1

        cursor = store.last_confirmed_message_id()
        async for message in tg.iter_messages(entity, min_id=cursor, reverse=True):
            _require_supported_message(message)
            operation = store.prepare_copy(message.id)
            destination_message_id = await _forward_copy(
                tg,
                source_peer=source_peer,
                destination_peer=destination_peer,
                operation=operation,
                account_user_id=me.id,
                account_alias=account_alias,
                mirror_id=record.mirror_id,
            )
            store.confirm_copy(message.id, destination_message_id)
            copied += 1

        return {
            "mirror": _result(
                record,
                destination_title=getattr(destination, "title", None),
            )["mirror"],
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
