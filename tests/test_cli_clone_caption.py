"""Clone sync splits a caption only Premium could post, losing no text.

A Premium author may caption media with up to 4096 characters; an account
without Premium is refused MEDIA_CAPTION_TOO_LONG past 1024. The clone keeps
the first part as the caption and sends the rest as the next message.
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


def texts(client):
    return [
        request
        for request in client.requests
        if isinstance(request, functions.messages.SendMessageRequest)
    ]


def test_a_premium_length_caption_is_split_and_its_rest_sent_next(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    first = "a" * 600 + "\n" + "b" * 300 + " "
    second = "c" * 500
    caption = first + "\n" + second
    bold = types.MessageEntityBold(offset=0, length=4)
    across = types.MessageEntityItalic(offset=800, length=400)
    beyond = types.MessageEntityItalic(offset=1300, length=10)
    client = CaptionClient(
        [message(2, media=PHOTO, message=caption, entities=[bold, across, beyond])]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    [refused] = client.refused
    post = sends(client)[-1]
    assert isinstance(post, functions.messages.SendMediaRequest)
    assert post.random_id == refused.random_id
    assert post.message == first.rstrip()
    head = units(post.message)
    assert post.entities == [
        bold,
        types.MessageEntityItalic(offset=800, length=head - 800),
    ]
    [rest] = texts(client)
    assert rest.message == second
    rest_start = units(caption) - units(second)
    assert rest.entities == [
        types.MessageEntityItalic(offset=0, length=1200 - rest_start),
        types.MessageEntityItalic(offset=1300 - rest_start, length=10),
    ]
    assert rest.reply_to is None
    assert [a["action"] for a in audits()[-2:]] == [
        "clone-sync-caption-fallback",
        "clone-sync-caption-rest",
    ]
    assert audits()[-1]["source_message_ids"] == [2]
    captured = capsys.readouterr()
    assert json.loads(captured.out)["sync"]["copied"] == 1
    post_id = state.load(clone_state.clone_id).dest_for(2)
    assert "clone source message 2: caption past 1024 characters" in captured.err
    assert f"continues in message {post_id + 1}" in captured.err


def test_a_caption_without_spaces_splits_at_the_limit_whole_characters(
    config_env, monkeypatch, capsys
):
    seed_clone()
    # 🔥 is two UTF-16 units; the limit falls inside the 513th one.
    client = CaptionClient([message(2, media=PHOTO, message="🔥" * 700)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert sends(client)[-1].message == "🔥" * 512
    assert [r.message for r in texts(client)] == ["🔥" * 188]


def test_a_reply_keeps_its_rest_in_the_same_thread(config_env, monkeypatch, capsys):
    """An open reply goes by file reference; its split must not fall back to a
    byte copy, and the rest answers the post so a thread keeps both."""
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

    post = sends(client)[-1]
    assert post.media == types.InputMediaPhoto(
        id=types.InputPhoto(id=70, access_hash=490, file_reference=b"ref")
    )
    assert units(post.message) == LIMIT
    assert client.downloads == [] and client.uploads == []
    [rest] = texts(client)
    assert rest.message == "b" * (2000 - LIMIT)
    assert rest.reply_to == types.InputReplyToMessage(
        reply_to_msg_id=state.load(clone_state.clone_id).dest_for(2)
    )


def test_each_album_caption_is_split_on_its_own(config_env, monkeypatch, capsys):
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
    assert [r.message for r in texts(client)] == ["c" * 76]
    assert audits()[-1]["source_message_ids"] == [2]


def test_a_failed_rest_leaves_the_post_mapped(config_env, monkeypatch, capsys):
    """The rest goes after the mapping is saved, so the next run never copies
    the post a second time."""
    clone_state = seed_clone()

    class RestRefused(CaptionClient):
        async def __call__(self, request):
            if isinstance(request, functions.messages.SendMessageRequest):
                raise telethon_errors.ChatWriteForbiddenError(request=None)
            return await super().__call__(request)

    client = RestRefused([message(2, media=PHOTO, message="e " * 700)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) != 0

    assert state.load(clone_state.clone_id).dest_for(2) is not None
    assert "its rest was not sent" in capsys.readouterr().err


def test_the_refusal_stands_when_no_caption_is_over_the_limit(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CaptionClient([message(2, media=PHOTO, message="d" * 900)], limit=800)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 1

    assert "caption-fallback" not in safety.audit_path().read_text()
    assert state.load(clone_state.clone_id).dest_for(2) is None
