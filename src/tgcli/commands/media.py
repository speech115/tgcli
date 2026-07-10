"""Media download command helpers (Phase 3; Telethon-only)."""

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re

from telethon import functions
from telethon import errors as telethon_errors

from tgcli.errors import NotFoundError, PolicyError
from tgcli.session import state_dir


PRIVATE_LINK = re.compile(r"(?:https?://)?t\.me/c/(\d+)/(\d+)/?$")
PUBLIC_LINK = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)/([1-9]\d*)/?$")
CHUNK_SIZE = 512 * 1024


@dataclass(frozen=True)
class MediaSource:
    chat: str | None
    message_id: int
    private_channel_id: int | None


def parse_source(source: str, message_id: int | None) -> MediaSource:
    private_match = PRIVATE_LINK.fullmatch(source)
    if private_match:
        if message_id is not None:
            raise NotFoundError(f"invalid media source: {source!r}")
        return MediaSource(
            chat=None,
            message_id=int(private_match.group(2)),
            private_channel_id=int(private_match.group(1)),
        )

    public_match = PUBLIC_LINK.fullmatch(source)
    if public_match:
        if message_id is not None:
            raise NotFoundError(f"invalid media source: {source!r}")
        return MediaSource(
            chat=f"@{public_match.group(1)}",
            message_id=int(public_match.group(2)),
            private_channel_id=None,
        )

    if message_id is None or message_id < 1:
        raise NotFoundError(f"invalid media source: {source!r}")
    return MediaSource(chat=source, message_id=message_id, private_channel_id=None)


def safe_filename(name: str | None, message_id: int) -> str:
    candidate = Path((name or "").replace("\\", "/")).name
    candidate = "".join(char if char.isprintable() else " " for char in candidate)
    candidate = candidate.strip(" .")
    return candidate if candidate else f"media-{message_id}.bin"


def destination_for(name: str, requested: str | None) -> Path:
    path = Path(requested).expanduser() if requested else Path.home() / "Downloads" / name
    if path.exists():
        raise PolicyError(f"output path already exists: {path}")
    return path


async def _resolve_private_entity(tg, channel_id: int, account_alias: str):
    async for dialog in tg.iter_dialogs():
        entity = dialog.entity
        if getattr(entity, "id", None) == channel_id:
            try:
                input_entity = await tg.get_input_entity(entity)
                await tg(functions.channels.GetChannelsRequest([input_entity]))
            except (
                ValueError,
                telethon_errors.ChannelInvalidError,
                telethon_errors.ChannelPrivateError,
            ):
                raise NotFoundError(
                    f"private channel {channel_id} not found; account {account_alias!r} lacks access"
                ) from None
            return entity
    raise NotFoundError(
        f"private channel {channel_id} not found; account {account_alias!r} lacks access"
    )


async def resolve_message(tg, source: MediaSource, account_alias: str):
    try:
        entity = (
            await _resolve_private_entity(tg, source.private_channel_id, account_alias)
            if source.private_channel_id is not None
            else await tg.get_entity(source.chat)
        )
    except ValueError:
        raise NotFoundError(f"dialog not found: {source.chat!r}") from None

    message = await tg.get_messages(entity, ids=source.message_id)
    if message is None or not getattr(message, "media", None):
        raise NotFoundError(f"downloadable media not found: {source.message_id}")
    return entity, message


def _source_label(source: MediaSource) -> str:
    chat = f"private:{source.private_channel_id}" if source.private_channel_id else source.chat
    return f"{chat}:{source.message_id}"


def _state_paths(source: MediaSource) -> tuple[Path, Path]:
    key = hashlib.sha256(_source_label(source).encode()).hexdigest()
    directory = state_dir() / "downloads"
    return directory / f"{key}.json", directory / f"{key}.part"


def _message_filename(message, message_id: int) -> str:
    file = getattr(message, "file", None)
    return safe_filename(getattr(file, "name", None), message_id)


def _message_size(message) -> int | None:
    file = getattr(message, "file", None)
    return getattr(file, "size", None)


def _write_state(
    path: Path, source: MediaSource, destination: Path, offset: int
) -> None:
    path.write_text(
        json.dumps(
            {
                "source": _source_label(source),
                "destination": str(destination),
                "offset": offset,
            }
        )
    )


def _resume_offset(state_path: Path, part_path: Path, source: MediaSource, destination: Path) -> int:
    if not state_path.exists():
        if part_path.exists():
            raise PolicyError(f"partial media download has no state: {part_path}")
        return 0
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"media download state is invalid: {state_path}") from exc
    if not part_path.exists():
        raise PolicyError(f"media download state has no partial file: {part_path}")
    if (
        state.get("source") != _source_label(source)
        or state.get("destination") != str(destination)
        or state.get("offset") != part_path.stat().st_size
    ):
        raise PolicyError(f"media download state does not match requested output: {state_path}")
    return part_path.stat().st_size


async def download_media(
    tg,
    source: MediaSource,
    account_alias: str,
    *,
    output: str | None = None,
    parallel: int = 1,
    progress=None,
) -> dict:
    if parallel < 1:
        raise PolicyError("parallel media download count must be positive")

    _, message = await resolve_message(tg, source, account_alias)
    destination = destination_for(_message_filename(message, source.message_id), output)
    state_path, part_path = _state_paths(source)
    offset = _resume_offset(state_path, part_path, source, destination)
    resumed = offset > 0

    part_path.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if parallel > 1:
        if resumed:
            raise PolicyError("parallel media download cannot resume an interrupted transfer")
        return await _download_parallel(
            tg,
            message,
            source,
            destination,
            part_path,
            parallel,
            progress,
        )

    if not resumed:
        _write_state(state_path, source, destination, offset)

    with part_path.open("ab" if resumed else "xb") as handle:
        async for chunk in tg.iter_download(
            message.media, offset=offset, request_size=CHUNK_SIZE
        ):
            handle.write(bytes(chunk))
            handle.flush()
            current = handle.tell()
            _write_state(state_path, source, destination, current)
            if progress:
                progress(current, _message_size(message))

    os.replace(part_path, destination)
    state_path.unlink(missing_ok=True)
    return {
        "source": _source_label(source),
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "resumed": resumed,
        "parallel": 1,
    }


async def _download_parallel(
    tg,
    message,
    source: MediaSource,
    destination: Path,
    part_path: Path,
    parallel: int,
    progress,
) -> dict:
    total = _message_size(message)
    if not isinstance(total, int) or total <= 0:
        raise NotFoundError(f"media size is unavailable for parallel download: {source.message_id}")

    with part_path.open("xb") as handle:
        handle.truncate(total)
    descriptor = os.open(part_path, os.O_WRONLY)
    downloaded = 0

    async def worker(index: int) -> None:
        nonlocal downloaded
        offset = index * CHUNK_SIZE
        async for chunk in tg.iter_download(
            message.media,
            offset=offset,
            stride=parallel * CHUNK_SIZE,
            request_size=CHUNK_SIZE,
        ):
            data = bytes(chunk)
            os.pwrite(descriptor, data, offset)
            offset += parallel * CHUNK_SIZE
            downloaded += len(data)
            if progress:
                progress(downloaded, total)

    try:
        await asyncio.gather(*(worker(index) for index in range(parallel)))
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)

    os.replace(part_path, destination)
    return {
        "source": _source_label(source),
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "resumed": False,
        "parallel": parallel,
    }


def to_rows(data: dict) -> list[tuple]:
    return [(data["path"], data["bytes"], data["resumed"], data["parallel"])]
