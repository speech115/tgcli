"""The clone reupload cache across failed and repeated downloads (ADR-0052).

``CacheMachine`` drives ``reupload.download_for_reupload`` the way successive
`clone sync` runs do: the same source messages downloaded again and again,
some downloads cut off mid-way, some media replaced at the source. One-chunk
media go through Telethon's ``download_media``, which picks the file name
(``src-<id>.jpg``) and never overwrites (``src-<id> (1).jpg``); larger media
stream into ``src-<id>.part``. After every step:

- a finished download is reused, never fetched again, until its media changes;
- what a download returns holds exactly the current media's bytes;
- the cache holds at most one data file per source message.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
from telethon.tl import types

from tests.fakes.clone import message
from tgcli.clone import reupload, state
from tgcli.transfer import CHUNK_SIZE

MESSAGES = (2, 3)
SIDECARS = (".done", ".part", ".offset")


def _photo_message(message_id: int, photo_id: int, size: int):
    photo = types.Photo(
        id=photo_id,
        access_hash=1,
        file_reference=b"",
        date=None,
        sizes=[types.PhotoSize(type="x", w=1, h=1, size=size)],
        dc_id=2,
    )
    return message(message_id, media=types.MessageMediaPhoto(photo=photo))


class Telegram:
    """Serves each message's current photo; can drop the connection."""

    def __init__(self):
        self.media: dict[int, tuple[int, int]] = {}  # photo id -> (size, byte)
        self.fetches = 0
        self.fail_after: int | None = None  # chunks served before the drop

    def payload(self, photo_id: int) -> bytes:
        size, byte = self.media[photo_id]
        return bytes([byte]) * size

    async def download_media(self, msg, file=None):
        self.fetches += 1
        if self.fail_after is not None:
            raise ConnectionError("dropped before the first byte")
        path = Path(f"{file}.jpg")
        copies = 0
        while path.exists():
            copies += 1
            path = Path(f"{file} ({copies}).jpg")
        path.write_bytes(self.payload(msg.media.photo.id))
        return str(path)

    async def iter_download(self, media, *, offset=0, request_size=None, **kw):
        self.fetches += 1
        data = self.payload(media.photo.id)
        served = 0
        position = offset
        while position < len(data):
            if self.fail_after is not None and served == self.fail_after:
                raise ConnectionError("dropped mid-stream")
            chunk = data[position : position + (request_size or CHUNK_SIZE)]
            position += len(chunk)
            served += 1
            yield chunk


class CacheMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.patch = pytest.MonkeyPatch()
        self.directory = tempfile.TemporaryDirectory()
        self.patch.setenv("TGCLI_STATE_DIR", self.directory.name)
        self.clone_state = state.CloneState.new(
            account_user_id=1, source_peer_id=2, source_title="S"
        )
        self.cache = reupload.ensure_cache_dir(self.clone_state)
        self.tg = Telegram()
        self.next_photo = 10
        self.current: dict[int, int] = {}  # message -> photo id
        self.finished: dict[int, int] = {}  # message -> photo id it holds
        for message_id in MESSAGES:
            self._replace(message_id, large=False)

    def _replace(self, message_id: int, *, large: bool) -> None:
        photo_id = self.next_photo
        self.next_photo += 1
        # Sizes never repeat: the cache tells media apart by byte size.
        size = (2 * CHUNK_SIZE if large else 100) + photo_id
        self.tg.media[photo_id] = (size, photo_id % 251)
        self.current[message_id] = photo_id

    @rule(message_id=st.sampled_from(MESSAGES), large=st.booleans())
    def source_replaces_media(self, message_id, large):
        self._replace(message_id, large=large)

    @rule(
        message_id=st.sampled_from(MESSAGES),
        fail_after=st.none() | st.integers(0, 2),
    )
    def download(self, message_id, fail_after):
        photo_id = self.current[message_id]
        size, _byte = self.tg.media[photo_id]
        msg = _photo_message(message_id, photo_id, size)
        fetches = self.tg.fetches
        self.tg.fail_after = fail_after
        try:
            path = asyncio.run(
                reupload.download_for_reupload(
                    self.tg, msg, self.cache, self.clone_state
                )
            )
        except ConnectionError:
            assert self.finished.get(message_id) != photo_id, (
                "a finished download was fetched again"
            )
            self.finished.pop(message_id, None)
            return
        finally:
            self.tg.fail_after = None
        if self.finished.get(message_id) == photo_id:
            assert self.tg.fetches == fetches, "a finished download was fetched again"
        assert Path(path).read_bytes() == self.tg.payload(photo_id)
        self.finished[message_id] = photo_id

    @invariant()
    def one_data_file_per_message(self):
        for message_id in MESSAGES:
            data = [
                path.name
                for path in self.cache.iterdir()
                if path.name.startswith(f"src-{message_id}")
                and not path.name.endswith(SIDECARS)
            ]
            assert len(data) <= 1, data

    def teardown(self):
        self.patch.undo()
        self.directory.cleanup()


CacheMachine.TestCase.settings = settings(
    max_examples=200, stateful_step_count=12, derandomize=True, deadline=None
)
TestCacheMachine = CacheMachine.TestCase
