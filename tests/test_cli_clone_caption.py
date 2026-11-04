"""Clone sync cuts a caption only Premium could post to the standard limit.

A Premium author may caption media with up to 4096 characters; an account
without Premium is refused MEDIA_CAPTION_TOO_LONG past 1024, and the clone
stalled on that message at every sync.
"""

import json

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tests.fakes.clone import SAMPLE, CloneReuploadClient, message, seed_clone
from tgcli import safety
from tgcli.cli import main
from tgcli.clone import state

LIMIT = 1024
PHOTO = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))


def units(text):
    return len(text.encode("utf-16-le")) // 2


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


class CaptionClient(CloneReuploadClient):
    """Refuses a media send whose caption passes ``limit`` UTF-16 units."""

    def __init__(self, messages, *, protected=True, limit=LIMIT):
        super().__init__(messages, protected=protected)
        self.limit = limit
        self.refused = []

    async def __call__(self, request):
        if isinstance(
            request,
            (
                functions.messages.SendMediaRequest,
                functions.messages.SendMultiMediaRequest,
            ),
        ):
            bodies = getattr(request, "multi_media", None) or [request]
            if any(units(body.message or "") > self.limit for body in bodies):
                self.refused.append(request)
                raise telethon_errors.MediaCaptionTooLongError(request=None)
        return await super().__call__(request)


def sends(client):
    return [
        request
        for request in client.requests
        if isinstance(
            request,
            (
                functions.messages.SendMediaRequest,
                functions.messages.SendMultiMediaRequest,
            ),
        )
    ]


def audits():
    return [json.loads(line) for line in safety.audit_path().read_text().splitlines()]


def test_a_premium_length_caption_is_cut_to_the_limit_and_sent(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    caption = "a" * 1500
    bold = types.MessageEntityBold(offset=0, length=4)
    across = types.MessageEntityItalic(offset=1000, length=100)
    beyond = types.MessageEntityItalic(offset=1200, length=10)
    client = CaptionClient(
        [message(2, media=PHOTO, message=caption, entities=[bold, across, beyond])]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    [refused] = client.refused
    sent = sends(client)[-1]
    assert isinstance(sent, functions.messages.SendMediaRequest)
    assert units(sent.message) == LIMIT
    assert sent.message == "a" * (LIMIT - 1) + "…"
    assert sent.entities == [
        bold,
        types.MessageEntityItalic(offset=1000, length=LIMIT - 1 - 1000),
    ]
    assert sent.random_id == refused.random_id
    assert audits()[-1]["action"] == "clone-sync-caption-fallback"
    assert audits()[-1]["source_message_ids"] == [2]
    captured = capsys.readouterr()
    assert json.loads(captured.out)["sync"]["copied"] == 1
    assert "clone source message 2: caption cut from 1500 to 1024" in captured.err
    assert state.load(clone_state.clone_id).dest_for(2) is not None


def test_the_cut_never_splits_a_surrogate_pair(config_env, monkeypatch, capsys):
    seed_clone()
    # 🔥 is two UTF-16 units; the limit falls inside the 512th one.
    client = CaptionClient([message(2, media=PHOTO, message="🔥" * 700)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sent = sends(client)[-1].message
    assert sent == "🔥" * 511 + "…"
    assert units(sent) <= LIMIT


def test_a_media_reference_send_is_cut_without_downloading(
    config_env, monkeypatch, capsys
):
    """An open reply goes by file reference; its cut must not fall back to a
    byte copy, which the same caption would fail again."""
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    photo = types.MessageMediaPhoto(
        photo=types.Photo(
            id=70, access_hash=490, file_reference=b"ref", date=None, sizes=[], dc_id=2
        )
    )
    reply = types.MessageReplyHeader(reply_to_msg_id=1)
    client = CaptionClient(
        [message(2, media=photo, reply_to=reply, message="b" * 2000)],
        protected=False,
    )
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sent = sends(client)[-1]
    assert sent.media == types.InputMediaPhoto(
        id=types.InputPhoto(id=70, access_hash=490, file_reference=b"ref")
    )
    assert units(sent.message) == LIMIT
    assert client.downloads == [] and client.uploads == []


def test_each_album_caption_is_cut_on_its_own(config_env, monkeypatch, capsys):
    seed_clone()
    client = CaptionClient(
        [
            message(2, grouped_id=77, media=PHOTO, message="c" * 1100),
            message(3, grouped_id=77, media=PHOTO, message="short"),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sent = sends(client)[-1]
    assert isinstance(sent, functions.messages.SendMultiMediaRequest)
    first, second = sent.multi_media
    assert units(first.message) == LIMIT
    assert second.message == "short"
    assert audits()[-1]["source_message_ids"] == [2]


def test_the_refusal_stands_when_no_caption_is_over_the_limit(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CaptionClient([message(2, media=PHOTO, message="d" * 900)], limit=800)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 1

    assert "caption-fallback" not in safety.audit_path().read_text()
    assert state.load(clone_state.clone_id).dest_for(2) is None
