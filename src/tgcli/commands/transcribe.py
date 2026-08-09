"""Server-side voice-message transcription (`tg transcribe`; ADR-0075).

Premium-only: calls `messages.transcribeAudio` and waits for the
asynchronous `updateTranscribedAudio` result, bounded by `--timeout`.
"""

import asyncio

from telethon import events
from telethon.errors import PremiumAccountRequiredError
from telethon.tl import functions, types

from tgcli import chatref
from tgcli.errors import CommandTimeoutError, NotFoundError, PolicyError


def _dialog_name(entity, chat: str) -> str:
    name = getattr(entity, "title", None) or getattr(entity, "first_name", None)
    return name or chat


async def _resolve_entity(tg, chat: str):
    try:
        return await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None


async def _fetch_voice_message(tg, entity, message_id: int):
    message = await tg.get_messages(entity, ids=message_id)
    if message is None:
        raise NotFoundError(f"message not found: {message_id}")
    if getattr(message, "voice", None) is None:
        raise NotFoundError(f"message {message_id} is not a voice message")
    return message


async def transcribe_message(tg, chat: str, message_id: int, timeout: float) -> dict:
    entity = await _resolve_entity(tg, chat)
    await _fetch_voice_message(tg, entity, message_id)

    # The result handler is registered before the request (race-free): the
    # RPC returns the first update (usually pending=True), and the text
    # arrives later through the normal update pipeline. The server assigns
    # the transcription_id in the RPC response, so an update that arrives
    # before the response is parked in `early` and replayed once the id is
    # known. After that, updates are matched on transcription_id (not just
    # msg_id): a concurrent transcription of the same message — another
    # dialog, or a re-run of this one — must not satisfy the wait
    # (ADR-0075).
    arrived = asyncio.Event()
    state: dict[str, object] = {}
    early: list[types.UpdateTranscribedAudio] = []

    def _settle(event: types.UpdateTranscribedAudio) -> None:
        if state.get("transcription_id") != event.transcription_id:
            return
        if event.pending:
            return
        state["text"] = event.text
        arrived.set()

    async def _on_update(event: types.UpdateTranscribedAudio) -> None:
        if state.get("transcription_id") is None:
            early.append(event)
            return
        _settle(event)

    handler = tg.add_event_handler(_on_update, events.Raw(types.UpdateTranscribedAudio))
    try:
        try:
            result = await tg(
                functions.messages.TranscribeAudioRequest(
                    peer=await tg.get_input_entity(entity), msg_id=message_id
                )
            )
        except PremiumAccountRequiredError as exc:
            raise PolicyError(
                "transcription requires a Telegram Premium account"
            ) from exc

        if result.pending:
            state["transcription_id"] = result.transcription_id
            for early_event in early:
                _settle(early_event)
            try:
                await asyncio.wait_for(arrived.wait(), timeout=timeout)
            except TimeoutError:
                raise CommandTimeoutError(
                    f"transcription did not complete within {timeout:.0f}s "
                    f"(transcription_id {result.transcription_id})",
                    transcription_id=result.transcription_id,
                ) from None
            text = state["text"]
        else:
            text = result.text
    finally:
        tg.remove_event_handler(handler, events.Raw(types.UpdateTranscribedAudio))

    return {
        "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        "message_id": message_id,
        "transcription": {
            "text": text,
            "transcription_id": result.transcription_id,
            "pending": False,
        },
    }


def to_rows(data: dict) -> list[tuple]:
    return [(str(data["message_id"]), data["transcription"]["text"])]
