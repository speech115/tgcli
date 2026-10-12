"""Clone batch send execution: forward, snapshot, and reupload orchestration.

RPC send orchestration for a synced batch lives here so the command module
reads as sync glue rather than transfer mechanics (ADR-0112).
"""

from __future__ import annotations

import secrets
import shutil

from telethon import errors as telethon_errors, utils as telethon_utils
from telethon.tl import functions, types

from tgcli import safety
from tgcli.clone import (
    attribution,
    quote_fallback,
    reforward,
    reupload,
    snapshot,
    state,
    topics,
    transport,
)
from tgcli.errors import PolicyError
from tgcli.output import note


def _body_text(message, author, plan) -> tuple[str, list | None]:
    return quote_fallback.apply_body(message, author, plan)


def _drops_author(leg, messages) -> bool:
    """A broadcast clone hides the source-forward header on the channel's own
    posts, but a post that is itself a forward keeps drop_author=False so
    Telegram restores its original forward header instead of erasing the origin.
    """
    return leg.source_kind == "broadcast" and not any(
        getattr(message, "fwd_from", None) is not None for message in messages
    )


async def reupload_batch(
    tg,
    destination,
    clone_state,
    account_alias,
    messages,
    random_ids,
    reply_to,
    author=None,
    plan=None,
    progress=None,
    *,
    by_reference=False,
):
    plan = plan or transport.TransportPlan(
        mode="reuploaded", reply_to=reply_to, reply_flattened=False, needs_author=False
    )

    def audit() -> None:
        safety.append_audit(
            "clone-sync-reupload",
            account_alias,
            {
                "clone_id": clone_state.clone_id,
                "source_message_ids": [m.id for m in messages],
            },
        )

    if by_reference:
        audit()
        # An unprotected source lets Telegram reuse its own file: no bytes
        # move, and a sticker stays a sticker. Any refusal but a flood falls
        # back to the byte copy below.
        try:
            return await _send(
                tg,
                destination,
                messages,
                random_ids,
                reply_to,
                author,
                plan,
                lambda message, album: _referenced(message),
            )
        except telethon_errors.FloodWaitError:
            raise
        except telethon_errors.RPCError as exc:
            note(
                f"warning: clone media by reference refused ({exc}); reuploading "
                f"source message(s) {[m.id for m in messages]}"
            )
    cache = reupload.cache_dir(clone_state)
    cache.mkdir(parents=True, exist_ok=True)
    downloads = {}
    for message in messages:
        if _has_media(message):
            downloads[message.id] = await reupload.download_for_reupload(
                tg, message, cache, clone_state, progress
            )
    if not by_reference:
        audit()

    async def uploaded(message, album):
        media = await reupload.uploaded_media(
            tg, message, downloads[message.id], progress
        )
        if not album:
            return media
        stored = await tg(
            functions.messages.UploadMediaRequest(peer=destination, media=media)
        )
        input_media = telethon_utils.get_input_media(stored)
        # The send carries the blur; UploadMedia's echo need not keep it.
        input_media.spoiler = media.spoiler
        return input_media

    return await _send(
        tg, destination, messages, random_ids, reply_to, author, plan, uploaded
    )


def _has_media(message) -> bool:
    media = getattr(message, "media", None)
    return media is not None and not isinstance(media, types.MessageMediaWebPage)


async def _referenced(message):
    return telethon_utils.get_input_media(message.media)


async def _send(
    tg, destination, messages, random_ids, reply_to, author, plan, media_for
):
    """One SendMessage/SendMedia/SendMultiMedia for the batch; ``media_for``
    turns a source message into the InputMedia this send carries."""
    if len(messages) == 1:
        message = messages[0]
        media = getattr(message, "media", None)
        text, entities = _body_text(message, author, plan)
        common = {
            "peer": destination,
            "message": text,
            "random_id": random_ids[0],
            "reply_to": reply_to,
            "entities": entities,
        }
        if not _has_media(message):
            request = functions.messages.SendMessageRequest(
                **common, no_webpage=media is None
            )
        else:
            request = functions.messages.SendMediaRequest(
                **common, media=await media_for(message, False)
            )
        return await tg(request)
    multi_media = []
    for index, (message, random_id) in enumerate(
        zip(messages, random_ids, strict=True)
    ):
        if not _has_media(message):
            raise PolicyError(f"clone album item is not reconstructable: {message.id}")
        text, entities = _body_text(message, author if index == 0 else None, plan)
        multi_media.append(
            types.InputSingleMedia(
                media=await media_for(message, True),
                random_id=random_id,
                message=text,
                entities=entities,
            )
        )
    return await tg(
        functions.messages.SendMultiMediaRequest(
            peer=destination, multi_media=multi_media, reply_to=reply_to
        )
    )


async def forward_batch(
    tg,
    source,
    destination,
    clone_state,
    leg,
    account_alias,
    messages,
    me,
    author_cache,
    plan,
    *,
    topic_dest=None,
    poll_votes: list | None = None,
    progress=None,
    reforward_cache: dict | None = None,
):
    source_ids = [message.id for message in messages]
    random_ids = [secrets.randbelow(2**63 - 1) + 1 for _ in messages]
    reply_to = plan.reply_to
    if topic_dest is not None:
        reply_to = topics.place(reply_to, topic_dest)
    top_msg_id = None if topic_dest in (None, topics.GENERAL_TOPIC_ID) else topic_dest

    # ADR-0050 Part B: a proven original outranks the Part A prefix, because
    # forwarding it carries Telegram's own header instead of describing one.
    proven = None
    if reforward_cache is not None and reforward.eligible(leg, messages, plan):
        proven = await reforward.locate(tg, clone_state, messages[0], reforward_cache)
    author = None
    if plan.needs_author and proven is None:
        if (
            leg.source_kind == "broadcast"
            and getattr(messages[0], "fwd_from", None) is not None
        ):
            author = await attribution.forwarded_author_of(
                tg, messages[0], author_cache
            )
        else:
            author = await attribution.author_of(
                tg, source, messages[0], me, author_cache
            )
    mode = plan.mode
    if proven is not None:
        group, group_message_id = proven
        mode = "forwarded"
        safety.append_audit(
            "clone-sync-reforward",
            account_alias,
            {
                "clone_id": clone_state.clone_id,
                "source_message_ids": source_ids,
                "group_message_ids": [group_message_id],
            },
        )
        response = await tg(
            functions.messages.ForwardMessagesRequest(
                from_peer=group,
                id=[group_message_id],
                random_id=random_ids,
                to_peer=destination,
                drop_author=False,
                top_msg_id=top_msg_id,
            ),
        )
    elif plan.mode == "snapshots":
        rendered_text, rendered_entities, poll_marker = await snapshot.render(
            tg,
            messages[0],
            peer=source,
            account_alias=account_alias,
        )
        if poll_marker is not None and poll_votes is not None:
            poll_votes.append(poll_marker)
        text, entities = attribution.with_prefix(
            rendered_text,
            rendered_entities,
            plan.body_prefix or "",
            plan.body_prefix_entities,
        )
        text, entities = attribution.prefixed(text, entities, author)
        safety.append_audit(
            "clone-sync-snapshot",
            account_alias,
            {"clone_id": clone_state.clone_id, "source_message_ids": source_ids},
        )
        response = await tg(
            functions.messages.SendMessageRequest(
                peer=destination,
                message=text,
                random_id=random_ids[0],
                reply_to=reply_to,
                no_webpage=True,
                entities=entities,
            ),
        )
    elif plan.mode == "forwarded":
        safety.append_audit(
            "clone-sync-forward",
            account_alias,
            {"clone_id": clone_state.clone_id, "source_message_ids": source_ids},
        )
        request = functions.messages.ForwardMessagesRequest(
            from_peer=source,
            id=source_ids,
            random_id=random_ids,
            to_peer=destination,
            drop_author=_drops_author(leg, messages),
            top_msg_id=top_msg_id,
        )
        response = await tg(request)
    else:
        response = await reupload_batch(
            tg,
            destination,
            clone_state,
            account_alias,
            messages,
            random_ids,
            reply_to,
            author,
            plan,
            progress,
            # Protected content is refused by reference, so only bytes copy it.
            by_reference=not getattr(source, "noforwards", False)
            and not any(getattr(m, "noforwards", False) for m in messages),
        )
    destination_ids = topics.confirmed_destination_ids(response, random_ids)
    for source_id, destination_id in zip(source_ids, destination_ids, strict=True):
        leg.record_mapping(source_id, destination_id)
    leg.cursor = source_ids[-1]
    state.save(clone_state)
    if mode == "reuploaded":
        # Only a confirmed send *and* a durable mapping save may clear the
        # cache — an incomplete confirmation or a crash before the save
        # must leave the downloaded bytes for the next run's resume
        # (ADR-0052; T12).
        shutil.rmtree(reupload.cache_dir(clone_state), ignore_errors=True)
    return len(source_ids), mode, plan.reply_flattened, plan.quote_flattened
