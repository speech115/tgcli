#!/usr/bin/env python3
"""Seed an owned private demo channel with every supported content kind.

Manual tool for mirror visual acceptance: fills a source channel with
text, a reply, real media files (ffmpeg/cwebp generated), an album, and
non-file media (poll, contact, geo, venue, dice). Refuses any peer that
is not a private broadcast channel owned by the account.

Usage:
    uv run python scripts/seed_demo_channel.py <chat> [--account ALIAS]
        [--kinds text,photo,...]
"""

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from telethon.errors import FloodWaitError
from telethon.tl import functions, types

from tgcli import chatref, config, safety, session
from tgcli.errors import ConfigError

BYTE_KINDS = {
    "photo": ("demo-photo.jpg", "image/jpeg", False),
    "document": ("demo-document.txt", "text/plain", True),
    "audio": ("demo-audio.mp3", "audio/mpeg", False),
    "voice": ("demo-voice.ogg", "audio/ogg", False),
    "video": ("demo-video.mp4", "video/mp4", False),
    "video_note": ("demo-note.mp4", "video/mp4", False),
    "animation": ("demo-animation.gif", "image/gif", False),
    "sticker": ("demo-sticker.webp", "image/webp", False),
}

NON_BYTE_KINDS = ("poll", "contact", "geo", "venue", "dice")

ALBUM_COLORS = ("0xff0000", "0x00ff00")


def planned_kinds() -> list[str]:
    return ["text", "reply", *BYTE_KINDS, "album", *NON_BYTE_KINDS]


def byte_attributes(kind: str) -> list:
    filename = BYTE_KINDS[kind][0]
    named = [types.DocumentAttributeFilename(filename)]
    if kind == "audio":
        return named + [
            types.DocumentAttributeAudio(
                duration=2, title="Demo audio", performer="tgcli"
            )
        ]
    if kind == "voice":
        return named + [types.DocumentAttributeAudio(duration=2, voice=True)]
    if kind == "video":
        return named + [
            types.DocumentAttributeVideo(
                duration=2, w=640, h=360, supports_streaming=True
            )
        ]
    if kind == "video_note":
        return named + [
            types.DocumentAttributeVideo(duration=2, w=240, h=240, round_message=True)
        ]
    if kind == "animation":
        return named + [types.DocumentAttributeAnimated()]
    if kind == "sticker":
        return named + [
            types.DocumentAttributeSticker(
                alt="🙂", stickerset=types.InputStickerSetEmpty()
            )
        ]
    return named


def preflight_tools() -> dict[str, str]:
    tools = {name: shutil.which(name) for name in ("ffmpeg", "cwebp")}
    missing = [name for name, path in tools.items() if path is None]
    if missing:
        raise ValueError(f"fixture tool unavailable: {', '.join(missing)}")
    return {name: path for name, path in tools.items() if path is not None}


def _run(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip().splitlines()[-1] if exc.stderr else "command failed"
        raise ValueError(f"fixture generation failed: {detail}") from exc


def materialize_fixture(kind: str, directory: Path, *, color: str = "0x3366cc") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / BYTE_KINDS[kind][0]
    if kind == "document":
        output.write_text("tgcli demo document fixture\n" * 64)
        return output
    tools = preflight_tools()
    common = [tools["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    if kind == "photo":
        command = common + [
            "-f", "lavfi", "-i", f"color=c={color}:s=512x512:d=1",
            "-frames:v", "1", "-c:v", "mjpeg", "-q:v", "2", str(output),
        ]
    elif kind == "audio":
        command = common + [
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", str(output),
        ]
    elif kind == "voice":
        command = common + [
            "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=2",
            "-ac", "1", "-c:a", "libopus", "-b:a", "24k",
            "-application", "voip", str(output),
        ]
    elif kind in {"video", "video_note"}:
        size = "640x360" if kind == "video" else "240x240"
        inputs = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24:duration=2"]
        audio = ["-an"]
        if kind == "video":
            inputs += [
                "-f", "lavfi", "-i",
                "sine=frequency=330:sample_rate=48000:duration=2",
            ]
            audio = ["-c:a", "aac", "-b:a", "64k", "-shortest"]
        command = common + inputs + [
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
        ] + audio + [str(output)]
    elif kind == "animation":
        command = common + [
            "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=15:duration=1",
            "-an", "-c:v", "gif", str(output),
        ]
    elif kind == "sticker":
        png = directory / "demo-sticker.png"
        _run(common + [
            "-f", "lavfi", "-i", f"color=c={color}:s=512x512:d=1",
            "-frames:v", "1", str(png),
        ])
        _run([tools["cwebp"], "-quiet", "-lossless", "-o", str(output), str(png)])
        png.unlink()
        return output
    else:
        raise ValueError(f"unknown byte fixture: {kind}")
    _run(command)
    return output


def non_byte_media(kind: str):
    geo = types.InputGeoPoint(lat=59.93, long=30.31)
    if kind == "poll":
        return types.InputMediaPoll(
            poll=types.Poll(
                id=0,
                hash=0,
                question=types.TextWithEntities(text="demo poll", entities=[]),
                answers=[
                    types.PollAnswer(
                        text=types.TextWithEntities(text="A", entities=[]),
                        option=b"0",
                    ),
                    types.PollAnswer(
                        text=types.TextWithEntities(text="B", entities=[]),
                        option=b"1",
                    ),
                ],
            )
        )
    if kind == "contact":
        return types.InputMediaContact(
            phone_number="+10000000000",
            first_name="Demo",
            last_name="Fixture",
            vcard="",
        )
    if kind == "geo":
        return types.InputMediaGeoPoint(geo_point=geo)
    if kind == "venue":
        return types.InputMediaVenue(
            geo_point=geo,
            title="Demo venue",
            address="Demo address",
            provider="demo",
            venue_id="demo-1",
            venue_type="demo",
        )
    if kind == "dice":
        return types.InputMediaDice(emoticon="🎲")
    raise ValueError(f"unknown non-byte kind: {kind}")


def require_owned_private_broadcast(entity) -> None:
    active_usernames = any(
        getattr(item, "active", False)
        for item in (getattr(entity, "usernames", None) or ())
    )
    if not (
        getattr(entity, "creator", False)
        and getattr(entity, "broadcast", False)
        and not getattr(entity, "megagroup", False)
        and getattr(entity, "username", None) is None
        and not active_usernames
    ):
        raise ValueError(
            "refusing to seed: target must be a private broadcast channel "
            "owned by this account"
        )


def _sent_message_ids(update) -> list[int]:
    for item in getattr(update, "updates", ()):
        message = getattr(item, "message", None)
        if message is not None and hasattr(message, "id"):
            return [message.id]
    for item in getattr(update, "updates", ()):
        if isinstance(item, types.UpdateMessageID):
            return [item.id]
    raise ValueError("could not extract sent message id")


async def seed_kind(tg, entity, kind: str, directory: Path, text_id: int | None):
    if kind == "text":
        message = await tg.send_message(entity, "demo text message")
        return [message.id]
    if kind == "reply":
        if text_id is None:
            raise ValueError("reply fixture needs the text fixture first")
        message = await tg.send_message(
            entity, "demo reply message", reply_to=text_id
        )
        return [message.id]
    if kind == "album":
        files = [
            materialize_fixture("photo", directory / f"album-{index}", color=color)
            for index, color in enumerate(ALBUM_COLORS)
        ]
        messages = await tg.send_file(entity, files, caption=["first", "second"])
        return [message.id for message in messages]
    if kind in BYTE_KINDS:
        _, mime_type, force_document = BYTE_KINDS[kind]
        path = materialize_fixture(kind, directory)
        message = await tg.send_file(
            entity,
            path,
            caption=f"demo {kind}",
            attributes=byte_attributes(kind),
            force_document=force_document,
            mime_type=mime_type,
        )
        return [message.id]
    update = await tg(
        functions.messages.SendMediaRequest(
            peer=entity,
            media=non_byte_media(kind),
            message="",
            random_id=int.from_bytes(os.urandom(8), "little", signed=True),
        )
    )
    return _sent_message_ids(update)


async def run(args) -> int:
    account = config.resolve_account(config.load_config(), args.account)
    kinds = args.kinds.split(",") if args.kinds else planned_kinds()
    unknown = sorted(set(kinds) - set(planned_kinds()))
    if unknown:
        print(f"seed: unknown kinds: {', '.join(unknown)}", file=sys.stderr)
        return 2
    failures = 0
    async with session.client(account, mutation_safe=True) as tg:
        entity = await tg.get_entity(chatref.parse(args.chat))
        require_owned_private_broadcast(entity)
        text_id = None
        with tempfile.TemporaryDirectory(prefix="tgcli-demo-seed-") as workdir:
            for kind in kinds:
                try:
                    ids = await seed_kind(tg, entity, kind, Path(workdir), text_id)
                except FloodWaitError as exc:
                    print(
                        f"seed: FLOOD_WAIT {exc.seconds}s at {kind}; "
                        "rerun later with the remaining kinds",
                        file=sys.stderr,
                    )
                    return 5
                except Exception as exc:
                    failures += 1
                    print(f"{kind}: blocked ({type(exc).__name__})")
                    continue
                if kind == "text":
                    text_id = ids[0]
                safety.append_audit(
                    "demo-seed",
                    account.alias,
                    {"chat": entity.id, "kind": kind, "message_ids": ids},
                )
                print(f"{kind}: seeded {ids}")
    return 1 if failures else 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chat", help="owned private broadcast channel")
    parser.add_argument("--account", default=None)
    parser.add_argument(
        "--kinds", default=None, help="comma-separated subset of kinds to seed"
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run(args))
    except ConfigError as exc:
        print(f"seed: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"seed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
