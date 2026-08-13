"""Clone batch send execution: forward, snapshot, and reupload orchestration.

RPC send orchestration for a synced batch lives here so the command module
reads as sync glue rather than transfer mechanics (ADR-0112).
"""

from __future__ import annotations

import secrets
import shutil

from telethon import utils as telethon_utils
from telethon.tl import functions, types

from tgcli import safety
from tgcli.clone import (
    attribution,
    cooldown,
    quote_fallback,
    reforward,
    reupload,
    snapshot,
    state,
    topics,
    transport,
)
from tgcli.errors import PolicyError

_mutate = cooldown.mutate


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
):
    plan = plan or transport.TransportPlan(
        mode="reuploaded", reply_to=reply_to, reply_flattened=False, needs_author=False
    )
    cache = reupload.cache_dir(clone_state)
    cache.mkdir(parents=True, exist_ok=True)
    downloads = {}
    for message in messages:
        media = getattr(message, "media", None)
        if media is None or isinstance(media, types.MessageMediaWebPage):
            continue
        downloads[message.id] = await reupload.download_for_reupload(
            tg, message, cache, clone_state, progress
        )
    safety.append_audit(
        "clone-sync-reupload",
        account_alias,
        {
            "clone_id": clone_state.clone_id,
            "source_message_ids": [m.id for m in messages],
        },
    )
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
        if media is None or isinstance(media, types.MessageMediaWebPage):
            request = functions.messages.SendMessageRequest(
                **common, no_webpage=media is None
            )
        else:
            request = functions.messages.SendMediaRequest(
                **common,
                media=await reupload.uploaded_media(
                    tg, message, downloads[message.id], progress
                ),
            )
        response = await _mutate(tg, request)
    else:
        multi_media = []
        for index, (message, random_id) in enumerate(
            zip(messages, random_ids, strict=True)
        ):
            if message.id not in downloads:
                raise PolicyError(
                    f"clone album item is not reconstructable: {message.id}"
                )
            uploaded = await reupload.uploaded_media(
                tg, message, downloads[message.id], progress
            )
            stored = await _mutate(
                tg,
                functions.messages.UploadMediaRequest(peer=destination, media=uploaded),
            )
            text, entities = _body_text(message, author if index == 0 else None, plan)
            multi_media.append(
                types.InputSingleMedia(
                    media=telethon_utils.get_input_media(stored),
                    random_id=random_id,
                    message=text,
                    entities=entities,
                )
            )
        request = functions.messages.SendMultiMediaRequest(
            peer=destination, multi_media=multi_media, reply_to=reply_to
        )
        response = await _mutate(tg, request)
    return response


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

    async def cooldown(make_awaitable):
        return await make_awaitable()

    # ADR-0050 Part B: a proven original outranks the Part A prefix, because
    # forwarding it carries Telegram's own header instead of describing one.
    proven = None
    if reforward_cache is not None and reforward.eligible(leg, messages, plan):
        proven = await reforward.locate(
            tg, clone_state, messages[0], reforward_cache, invoke=cooldown
        )
    author = None
    if plan.needs_author and proven is None:
        if (
            leg.source_kind == "broadcast"
            and getattr(messages[0], "fwd_from", None) is not None
        ):
            author = await attribution.forwarded_author_of(
                tg, messages[0], author_cache, cooldown
            )
        else:
            author = await attribution.author_of(
                tg, source, messages[0], me, author_cache, cooldown
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
        response = await _mutate(
            tg,
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
            invoke=lambda make_awaitable: make_awaitable(),
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
        response = await _mutate(
            tg,
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
        response = await _mutate(tg, request)
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
