"""Lean faithful-mirror command facade (ADR-0014)."""

import tempfile
from datetime import datetime, timezone
from math import ceil
from pathlib import Path

from telethon import errors as telethon_errors
from telethon import utils as telethon_utils
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


def _store_call(method, /, *args, **kwargs):
    try:
        return method(*args, **kwargs)
    except ValueError:
        raise PolicyError(
            "local mirror state is invalid; manual repair is required"
        ) from None


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
    store = MirrorStore()
    record = _store_call(store.create, me.id, entity.id, entity.title)
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
        record = _store_call(store.create, me.id, entity.id, entity.title)
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
                _store_call(store.mark_create_blocked)
                raise PolicyError("mirror destination marker matched multiple channels")
            if wrong_shape:
                _store_call(store.mark_create_blocked)
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
                _store_call(
                    store.mark_create_dispatched,
                    marker,
                    datetime.now(timezone.utc),
                )
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
            record = _store_call(store.authorize, destination.id)

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


def _response_updates(response) -> tuple[object, ...]:
    updates = getattr(response, "updates", None)
    if updates is None:
        update = getattr(response, "update", None)
        updates = () if update is None else (update,)
    return tuple(updates)


def _destination_message_ids(
    response, random_ids: list[int]
) -> dict[int, int]:
    if isinstance(response, types.UpdateShortSentMessage):
        destination_id = response.id
        if (
            len(random_ids) != 1
            or isinstance(destination_id, bool)
            or not isinstance(destination_id, int)
            or destination_id <= 0
        ):
            raise PolicyError("Telegram did not confirm the complete mirrored batch")
        return {random_ids[0]: destination_id}
    confirmations: dict[int, int] = {}
    for update in _response_updates(response):
        if not isinstance(update, types.UpdateMessageID):
            continue
        if update.random_id in confirmations:
            raise PolicyError("Telegram returned a duplicate mirror confirmation")
        confirmations[update.random_id] = update.id
    if set(confirmations) != set(random_ids) or len(confirmations) != len(random_ids):
        raise PolicyError("Telegram did not confirm the complete mirrored batch")
    destination_ids = list(confirmations.values())
    if any(
        isinstance(destination_id, bool)
        or not isinstance(destination_id, int)
        or destination_id <= 0
        for destination_id in destination_ids
    ):
        raise PolicyError("Telegram returned an invalid mirror confirmation")
    if len(set(destination_ids)) != len(destination_ids):
        raise PolicyError("Telegram returned duplicate destination messages")
    return confirmations


_NATIVE_MEDIA_TYPES = (
    types.MessageMediaWebPage,
    types.MessageMediaPhoto,
    types.MessageMediaDocument,
)


def _require_supported_message(message) -> None:
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


def _reply_peer_is_source(peer, source_peer_id: int) -> bool:
    return isinstance(peer, types.PeerChannel) and peer.channel_id == source_peer_id


def _normalized_reply(message, source_peer_id: int):
    header = getattr(message, "reply_to", None)
    if header is None:
        return None
    if not isinstance(header, types.MessageReplyHeader):
        raise PolicyError(
            f"mirror reply shape is not supported; stopped at source message {message.id}"
        )
    if (
        bool(header.reply_to_scheduled)
        or bool(header.forum_topic)
        or bool(header.reply_to_ephemeral)
        or header.reply_to_top_id is not None
        or header.todo_item_id is not None
        or header.poll_option is not None
        or header.reply_from is not None
        or header.reply_media is not None
    ):
        raise PolicyError(
            f"mirror reply shape is not supported; stopped at source message {message.id}"
        )
    if header.reply_to_peer_id is not None and not _reply_peer_is_source(
        header.reply_to_peer_id, source_peer_id
    ):
        raise PolicyError(
            f"cross-peer mirror replies are not supported; "
            f"stopped at source message {message.id}"
        )
    parent_id = header.reply_to_msg_id
    if isinstance(parent_id, bool) or not isinstance(parent_id, int) or parent_id <= 0:
        raise PolicyError(
            f"mirror reply parent is invalid; stopped at source message {message.id}"
        )
    if header.quote_text is not None and not isinstance(header.quote_text, str):
        raise PolicyError(
            f"mirror reply quote is invalid; stopped at source message {message.id}"
        )
    quote_entities = tuple(header.quote_entities or ())
    if quote_entities and header.quote_text is None:
        raise PolicyError(
            f"mirror reply quote is invalid; stopped at source message {message.id}"
        )
    if header.quote_offset is not None and (
        isinstance(header.quote_offset, bool)
        or not isinstance(header.quote_offset, int)
        or header.quote_offset < 0
        or header.quote_text is None
    ):
        raise PolicyError(
            f"mirror reply quote is invalid; stopped at source message {message.id}"
        )
    return (
        parent_id,
        header.quote_text,
        quote_entities,
        header.quote_offset,
    )


def _validate_batch(
    messages: list[object], store: MirrorStore, source_peer_id: int
):
    if not messages:
        raise PolicyError("mirror batch is empty")
    message_ids = [getattr(message, "id", None) for message in messages]
    if any(
        isinstance(message_id, bool)
        or not isinstance(message_id, int)
        or message_id <= 0
        for message_id in message_ids
    ) or len(set(message_ids)) != len(message_ids):
        raise PolicyError("mirror batch contains invalid source message ids")

    grouped_ids = [getattr(message, "grouped_id", None) for message in messages]
    grouped_id = grouped_ids[0]
    if grouped_id is None:
        if len(messages) != 1 or any(item is not None for item in grouped_ids):
            raise PolicyError("mirror batch membership is inconsistent")
    else:
        if (
            isinstance(grouped_id, bool)
            or not isinstance(grouped_id, int)
            or any(item != grouped_id for item in grouped_ids)
        ):
            raise PolicyError("mirror album membership is inconsistent")

    for message in messages:
        _require_supported_message(message)

    replies = [
        _normalized_reply(message, source_peer_id) for message in messages
    ]
    leading = replies[0]
    if leading is None:
        if any(reply is not None for reply in replies[1:]):
            raise PolicyError("mirror album reply appears only after its leading item")
        return None
    if any(reply is not None and reply != leading for reply in replies[1:]):
        raise PolicyError("mirror album reply metadata is inconsistent")

    parent_id, quote_text, quote_entities, quote_offset = leading
    destination_parent_id = _store_call(store.destination_message_id, parent_id)
    if destination_parent_id is None:
        raise PolicyError(
            f"mirror reply parent is not confirmed: {parent_id}"
        )
    return types.InputReplyToMessage(
        reply_to_msg_id=destination_parent_id,
        quote_text=quote_text,
        quote_entities=list(quote_entities) or None,
        quote_offset=quote_offset,
    )


def _ordered_recovered_messages(
    recovered, operations: list[CopyOperation]
) -> list[object]:
    if not isinstance(recovered, (list, tuple)):
        raise PolicyError("Telegram did not return the complete pending mirror batch")
    by_id: dict[int, object] = {}
    for message in recovered:
        message_id = getattr(message, "id", None)
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or message_id <= 0
            or message_id in by_id
        ):
            raise PolicyError("Telegram returned an invalid pending mirror batch")
        by_id[message_id] = message
    expected_ids = {operation.source_message_id for operation in operations}
    if set(by_id) != expected_ids:
        raise PolicyError("Telegram did not return the exact pending mirror batch")
    return [by_id[operation.source_message_id] for operation in operations]


async def _dispatch_prepared_batch(
    tg,
    *,
    operations: list[CopyOperation],
    store: MirrorStore,
    reply_to,
    source_peer,
    destination_peer,
    account_user_id: int,
    account_alias: str,
    mirror_id: str,
) -> int:
    source_message_ids = [operation.source_message_id for operation in operations]
    random_ids = [operation.random_id for operation in operations]
    safety.append_audit(
        "mirror-sync-forward",
        account_alias,
        {
            "mirror_id": mirror_id,
            "source_message_ids": source_message_ids,
            "random_ids": random_ids,
        },
    )
    response = await _dispatch_mutation(
        tg,
        functions.messages.ForwardMessagesRequest(
            from_peer=source_peer,
            id=source_message_ids,
            random_id=random_ids,
            to_peer=destination_peer,
            drop_author=True,
            reply_to=reply_to,
        ),
        account_user_id,
    )
    confirmations = _destination_message_ids(response, random_ids)
    _store_call(
        store.confirm_batch,
        {
            operation.source_message_id: confirmations[operation.random_id]
            for operation in operations
        },
    )
    return len(operations)


def _batch_uses_reupload(source_entity, messages: list[object]) -> bool:
    return bool(getattr(source_entity, "noforwards", False)) or any(
        getattr(message, "noforwards", False)
        or getattr(message, "reply_to", None) is not None
        for message in messages
    )


def _message_entities(message) -> list | None:
    entities = getattr(message, "entities", None)
    return list(entities) if entities else None


def _message_caption(message) -> str:
    return getattr(message, "message", None) or ""


async def _download_batch_media(
    tg, messages: list[object], directory: Path
) -> dict[int, Path]:
    downloads: dict[int, Path] = {}
    for message in messages:
        media = getattr(message, "media", None)
        if media is None or isinstance(media, types.MessageMediaWebPage):
            continue
        target = directory / f"src-{message.id}"
        downloaded = await tg.download_media(message, file=target)
        if downloaded is None:
            raise PolicyError(
                f"mirror media download failed; stopped at source message {message.id}"
            )
        downloads[message.id] = Path(downloaded)
    return downloads


async def _uploaded_input_media(tg, message, path: Path, account_user_id: int):
    try:
        input_file = await tg.upload_file(str(path))
    except telethon_errors.FloodWaitError as exc:
        record_cooldown(account_user_id, exc.seconds)
        raise
    media = message.media
    if isinstance(media, types.MessageMediaPhoto):
        return types.InputMediaUploadedPhoto(file=input_file)
    document = media.document
    return types.InputMediaUploadedDocument(
        file=input_file,
        mime_type=getattr(document, "mime_type", None)
        or "application/octet-stream",
        attributes=list(getattr(document, "attributes", None) or ()),
    )


async def _dispatch_reupload_batch(
    tg,
    *,
    operations: list[CopyOperation],
    messages: list[object],
    store: MirrorStore,
    reply_to,
    destination_peer,
    account_user_id: int,
    account_alias: str,
    mirror_id: str,
) -> int:
    source_message_ids = [operation.source_message_id for operation in operations]
    random_ids = [operation.random_id for operation in operations]
    with tempfile.TemporaryDirectory(prefix="tgcli-mirror-reupload-") as workdir:
        downloads = await _download_batch_media(tg, messages, Path(workdir))
        safety.append_audit(
            "mirror-sync-reupload",
            account_alias,
            {
                "mirror_id": mirror_id,
                "source_message_ids": source_message_ids,
                "random_ids": random_ids,
            },
        )
        if len(messages) == 1:
            message = messages[0]
            media = getattr(message, "media", None)
            if media is None or isinstance(media, types.MessageMediaWebPage):
                request = functions.messages.SendMessageRequest(
                    peer=destination_peer,
                    message=_message_caption(message),
                    no_webpage=media is None,
                    random_id=random_ids[0],
                    reply_to=reply_to,
                    entities=_message_entities(message),
                )
            else:
                uploaded = await _uploaded_input_media(
                    tg, message, downloads[message.id], account_user_id
                )
                request = functions.messages.SendMediaRequest(
                    peer=destination_peer,
                    media=uploaded,
                    message=_message_caption(message),
                    random_id=random_ids[0],
                    reply_to=reply_to,
                    entities=_message_entities(message),
                )
            response = await _dispatch_mutation(tg, request, account_user_id)
        else:
            multi_media = []
            for message, random_id in zip(messages, random_ids, strict=True):
                if message.id not in downloads:
                    raise PolicyError(
                        f"mirror album item is not reconstructable; "
                        f"stopped at source message {message.id}"
                    )
                uploaded = await _uploaded_input_media(
                    tg, message, downloads[message.id], account_user_id
                )
                stored = await _dispatch_mutation(
                    tg,
                    functions.messages.UploadMediaRequest(
                        peer=destination_peer, media=uploaded
                    ),
                    account_user_id,
                )
                multi_media.append(
                    types.InputSingleMedia(
                        media=telethon_utils.get_input_media(stored),
                        random_id=random_id,
                        message=_message_caption(message),
                        entities=_message_entities(message),
                    )
                )
            response = await _dispatch_mutation(
                tg,
                functions.messages.SendMultiMediaRequest(
                    peer=destination_peer,
                    multi_media=multi_media,
                    reply_to=reply_to,
                ),
                account_user_id,
            )
    confirmations = _destination_message_ids(response, random_ids)
    _store_call(
        store.confirm_batch,
        {
            operation.source_message_id: confirmations[operation.random_id]
            for operation in operations
        },
    )
    return len(operations)


async def sync_text(tg, source: str, account_alias: str) -> dict:
    entity, me = await _resolve(tg, source)
    with account_mutation_lock(me.id):
        _enforce_cooldown(me.id)
        store = MirrorStore()
        record = _store_call(store.create, me.id, entity.id, entity.title)
        if not record.authorized:
            raise PolicyError("mirror source is not authorized; run mirror init --commit")

        pending_batches = _store_call(store.pending_batches)
        destination = await _destination_entity(tg, record)
        source_peer = await tg.get_input_entity(entity)
        destination_peer = await tg.get_input_entity(destination)
        copied = 0
        for operations in pending_batches:
            operations = sorted(operations, key=lambda item: item.batch_index)
            source_message_ids = [
                operation.source_message_id for operation in operations
            ]
            recovered = await tg.get_messages(entity, ids=source_message_ids)
            messages = _ordered_recovered_messages(recovered, operations)
            reply_to = _validate_batch(messages, store, entity.id)
            if _batch_uses_reupload(entity, messages):
                copied += await _dispatch_reupload_batch(
                    tg,
                    operations=operations,
                    messages=messages,
                    store=store,
                    reply_to=reply_to,
                    destination_peer=destination_peer,
                    account_user_id=me.id,
                    account_alias=account_alias,
                    mirror_id=record.mirror_id,
                )
            else:
                copied += await _dispatch_prepared_batch(
                    tg,
                    operations=operations,
                    store=store,
                    reply_to=reply_to,
                    source_peer=source_peer,
                    destination_peer=destination_peer,
                    account_user_id=me.id,
                    account_alias=account_alias,
                    mirror_id=record.mirror_id,
                )

        cursor = _store_call(store.last_confirmed_message_id)
        skipped_service = 0
        active_album: list[object] = []
        seen_group_ids: set[int] = set()

        async def copy_new_batch(messages: list[object]) -> int:
            reply_to = _validate_batch(messages, store, entity.id)
            source_message_ids = [message.id for message in messages]
            grouped_id = getattr(messages[0], "grouped_id", None)
            batch_key = (
                f"single:{source_message_ids[0]}"
                if grouped_id is None
                else f"album:{grouped_id}"
            )
            operations = _store_call(
                store.prepare_batch,
                source_message_ids,
                batch_key=batch_key,
            )
            if _batch_uses_reupload(entity, messages):
                return await _dispatch_reupload_batch(
                    tg,
                    operations=operations,
                    messages=messages,
                    store=store,
                    reply_to=reply_to,
                    destination_peer=destination_peer,
                    account_user_id=me.id,
                    account_alias=account_alias,
                    mirror_id=record.mirror_id,
                )
            return await _dispatch_prepared_batch(
                tg,
                operations=operations,
                store=store,
                reply_to=reply_to,
                source_peer=source_peer,
                destination_peer=destination_peer,
                account_user_id=me.id,
                account_alias=account_alias,
                mirror_id=record.mirror_id,
            )

        async for message in tg.iter_messages(entity, min_id=cursor, reverse=True):
            if getattr(message, "action", None) is not None:
                if active_album:
                    copied += await copy_new_batch(active_album)
                    active_album = []
                skipped_service += 1
                continue
            grouped_id = getattr(message, "grouped_id", None)
            if grouped_id is None:
                if active_album:
                    copied += await copy_new_batch(active_album)
                    active_album = []
                copied += await copy_new_batch([message])
                continue

            if isinstance(grouped_id, bool) or not isinstance(grouped_id, int):
                raise PolicyError(
                    f"mirror album group id is invalid at source message {message.id}"
                )
            if active_album and grouped_id == getattr(
                active_album[0], "grouped_id", None
            ):
                active_album.append(message)
                continue
            if active_album:
                copied += await copy_new_batch(active_album)
                active_album = []
            if grouped_id in seen_group_ids:
                raise PolicyError(
                    f"mirror album group {grouped_id} is not contiguous"
                )
            seen_group_ids.add(grouped_id)
            active_album = [message]

        if active_album:
            copied += await copy_new_batch(active_album)

        return {
            "mirror": _result(
                record,
                destination_title=getattr(destination, "title", None),
            )["mirror"],
            "sync": {
                "copied": copied,
                "skipped_service": skipped_service,
                "last_confirmed_message_id": _store_call(
                    store.last_confirmed_message_id
                ),
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
                data["sync"]["skipped_service"],
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
