"""clone sync: skipped, replaced and grouped content, lost keyboards."""

import json
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tests.fakes.clone import (
    CloneCommentsClient,
    CloneReuploadClient,
    CloneSyncClient,
    _inline_keyboard,
    anchor,
    channel,
    message,
    poll_media,
    seed_clone,
    seed_comments_clone,
)

# A fixture: the redundant alias marks an explicit re-export for ruff.
from tests.fakes.clone_env import config_env as config_env
from tgcli import safety, session
from tgcli.cli import main
from tgcli.clone import state


def test_clone_sync_skips_service_and_reports_unsupported_messages(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    poll_media = type("MessageMediaPoll", (), {})()
    dice_media = type("MessageMediaDice", (), {})()
    client = CloneSyncClient(
        [
            message(1, action=object()),
            message(2, media=poll_media),
            message(3, media=dice_media),
            message(4),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    sync.pop("participants")
    assert sync.pop("pinned") == {
        "source_id": None,
        "destination_id": None,
        "status": "unmapped",
    }
    assert sync == {
        "copied": 1,
        "forwarded": 1,
        "reuploaded": 0,
        "snapshots": 0,
        "reply_flattened": 0,
        "quote_flattened": [],
        "markup_dropped": [],
        "poll_votes": [],
        "skipped_service": 1,
        "skipped_unsupported": [
            {"id": 2, "kind": "MessageMediaPoll"},
            {"id": 3, "kind": "MessageMediaDice"},
        ],
        "skipped_autoforward": 0,
        "topics_created": 0,
        "cursor": 4,
        "discussion_cursor": 0,
        "more": False,
    }
    assert [request.id for request in client.requests] == [[4]]
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 4
    assert saved.dest_for(4) == 2


def test_clone_sync_reports_every_unsupported_skip_before_a_later_flood(
    config_env, monkeypatch, capsys
):
    """Skipped rows are behind the durable cursor after the flood, so every
    affected source message must already have its own surviving stderr row."""
    clone_state = seed_clone()
    dice_media = type("MessageMediaDice", (), {})()
    game_media = type("MessageMediaGame", (), {})()

    class FloodAfterUnsupportedSkips(CloneSyncClient):
        async def __call__(self, request):
            if isinstance(request, functions.messages.ForwardMessagesRequest):
                raise telethon_errors.FloodWaitError(request=request, capture=600)
            return await super().__call__(request)

    client = FloodAfterUnsupportedSkips(
        [
            message(2, media=dice_media),
            message(3, media=game_media),
            message(4),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5

    captured = capsys.readouterr()
    assert "sync" not in json.loads(captured.out)
    warnings = [
        line for line in captured.err.splitlines() if "skipped unsupported" in line
    ]
    assert warnings == [
        "warning: source message 2 skipped unsupported MessageMediaDice",
        "warning: source message 3 skipped unsupported MessageMediaGame",
    ]
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 3
    assert saved.dest_for(4) is None


@pytest.mark.parametrize(
    "media",
    [
        types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7), ttl_seconds=5),
        types.MessageMediaDocument(document=types.DocumentEmpty(id=8), ttl_seconds=5),
    ],
)
def test_clone_sync_skips_view_once_media(media, config_env, monkeypatch, capsys):
    clone_state = seed_clone()
    client = CloneSyncClient([message(2, media=media)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 0
    assert sync["skipped_unsupported"] == [
        {
            "id": 2,
            "kind": f"{type(media).__name__}TTL",
        }
    ]
    assert client.requests == []
    assert state.load(clone_state.clone_id).cursor == 2


@pytest.mark.parametrize(
    "multiple_choice",
    [False, True],
)
def test_clone_sync_replaces_poll_with_result_snapshot(
    multiple_choice, config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneReuploadClient(
        [message(2, media=poll_media(multiple_choice=multiple_choice))]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["skipped_unsupported"] == []
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.message.startswith("📊 Результаты опроса\n\nChoose\n\n")
    assert "Choose" in request.message
    assert "First\n████░░░░░░ 40% · 4 голоса" in request.message
    assert "Second\n██████░░░░ 60% · 6 голосов" in request.message
    assert request.message.endswith("Проголосовало: 10")
    assert "snapshot" not in request.message.casefold()
    assert "clone" not in request.message.casefold()
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_poll_without_breakdown_casts_and_retracts_vote(
    config_env, monkeypatch, capsys
):
    seed_clone()
    media = poll_media(results=[], total_voters=10, public_voters=False, quiz=False)
    client = CloneReuploadClient([message(2, media=media)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    votes = [
        req
        for req in client.requests
        if isinstance(req, functions.messages.SendVoteRequest)
    ]
    assert len(votes) == 2
    assert votes[0].options == [b"a"]
    assert votes[1].options == []
    [send] = [
        req
        for req in client.requests
        if isinstance(req, functions.messages.SendMessageRequest)
    ]
    assert "40% · 4 голоса" in send.message
    assert "Проголосовало: 10" in send.message
    assert out["sync"]["poll_votes"] == [{"message_id": 2, "status": "captured"}]


def test_clone_sync_no_send_blocks_before_reaching_a_poll(monkeypatch):
    """TGCLI_NO_SEND cannot let a poll vote slip through, because it stops
    the whole sync in preflight (ADR-0048 §4). The renderer's own gate is a
    second line of defence, unit-tested in test_clone_snapshot.py."""
    from tgcli import cli

    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        session, "client", lambda account: pytest.fail("session opened")
    )

    assert main(["clone", "sync", "@source", "--json"]) == 2
    assert not safety.audit_path().exists()


def test_clone_sync_replaces_unavailable_story_with_named_placeholder(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    story = types.MessageMediaStory(peer=types.PeerUser(88), id=5558)

    class StoryClient(CloneReuploadClient):
        async def get_entity(self, ref):
            if isinstance(ref, types.PeerUser):
                return SimpleNamespace(
                    id=88,
                    first_name="Andrey",
                    last_name="Kozlov",
                    username="targetdaddy",
                )
            return await super().get_entity(ref)

    client = StoryClient([message(2, media=story)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["skipped_unsupported"] == []
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.message == "Stories недоступна\nАвтор: Andrey Kozlov"
    [entity] = request.entities
    prefix = "Stories недоступна\nАвтор: "
    assert isinstance(entity, types.MessageEntityTextUrl)
    assert entity.offset == len(prefix.encode("utf-16-le")) // 2
    assert entity.length == len("Andrey Kozlov".encode("utf-16-le")) // 2
    assert entity.url == "https://t.me/targetdaddy"
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_maps_reply_to_story_placeholder(config_env, monkeypatch, capsys):
    clone_state = seed_clone()
    story = types.MessageMediaStory(peer=types.PeerChannel(77), id=346)

    class StoryReplyClient(CloneReuploadClient):
        async def get_entity(self, ref):
            if isinstance(ref, types.PeerChannel) and ref.channel_id == 77:
                return channel(77, "Story source", username="storysource")
            return await super().get_entity(ref)

    client = StoryReplyClient(
        [
            message(2, media=story),
            message(
                3, message="Reply", reply_to=types.MessageReplyHeader(reply_to_msg_id=2)
            ),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 2
    assert isinstance(client.requests[0], functions.messages.SendMessageRequest)
    assert client.requests[0].message == "Stories недоступна\nАвтор: Story source"
    assert client.requests[0].entities[0].url == "https://t.me/storysource"
    assert isinstance(client.requests[1], functions.messages.SendMessageRequest)
    assert client.requests[1].reply_to.reply_to_msg_id == 2
    saved = state.load(clone_state.clone_id)
    assert [saved.dest_for(2), saved.dest_for(3)] == [2, 3]


@pytest.mark.parametrize(
    "media",
    [
        types.MessageMediaWebPage(webpage=types.WebPageEmpty(id=6)),
        types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7)),
        types.MessageMediaDocument(document=types.DocumentEmpty(id=8)),
    ],
)
def test_clone_sync_forwards_native_media_without_flattening_caption(
    media, config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(2, media=media, message="photo caption")])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["sync"]["copied"] == 1
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 2
    assert saved.dest_for(2) == 2
    assert [request.id for request in client.requests] == [[2]]
    assert client.requests[0].drop_media_captions is None


def test_clone_sync_keeps_grouped_id_zero_album_atomic_and_in_position(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    document = types.MessageMediaDocument(document=types.DocumentEmpty(id=8))
    client = CloneSyncClient(
        [
            message(2),
            message(3, media=photo, grouped_id=0),
            message(4, media=document, grouped_id=0),
            message(5),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    result["sync"].pop("participants")
    assert result["sync"].pop("pinned") == {
        "source_id": None,
        "destination_id": None,
        "status": "unmapped",
    }
    assert result["sync"] == {
        "copied": 4,
        "forwarded": 4,
        "reuploaded": 0,
        "snapshots": 0,
        "reply_flattened": 0,
        "quote_flattened": [],
        "markup_dropped": [],
        "poll_votes": [],
        "skipped_service": 0,
        "skipped_unsupported": [],
        "skipped_autoforward": 0,
        "topics_created": 0,
        "cursor": 5,
        "discussion_cursor": 0,
        "more": False,
    }
    assert [request.id for request in client.requests] == [[2], [3, 4], [5]]
    assert len(client.requests[1].random_id) == 2
    assert len(set(client.requests[1].random_id)) == 2
    saved = state.load(clone_state.clone_id)
    assert [saved.dest_for(source_id) for source_id in (2, 3, 4, 5)] == [2, 3, 4, 5]


def test_clone_sync_rejects_duplicate_album_confirmation_atomically(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()

    class DuplicateConfirmationClient(CloneSyncClient):
        async def __call__(self, request):
            response = await super().__call__(request)
            response.updates.append(response.updates[0])
            return response

    client = DuplicateConfirmationClient(
        [message(2, grouped_id=44), message(3, grouped_id=44)]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "complete cloned batch" in capsys.readouterr().err
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0
    assert saved.id_map == {}


def test_clone_sync_reports_the_buttons_a_reupload_cannot_carry(
    config_env, monkeypatch, capsys
):
    """#82: a protected source travels by reupload, and a user account cannot
    reattach the bot keyboard, so the loss is named instead of dropped."""
    seed_clone()
    client = CloneReuploadClient(
        [message(2, message="with buttons", reply_markup=_inline_keyboard())],
        protected=True,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    captured = capsys.readouterr()
    sync = json.loads(captured.out)["sync"]
    assert sync["reuploaded"] == 1
    assert sync["markup_dropped"] == [
        {
            "id": 2,
            "buttons": [
                {"type": "KeyboardButtonUrl", "text": "Open"},
                {"type": "KeyboardButtonCallback", "text": "Vote"},
            ],
        }
    ]
    assert "lost 2 bot button(s)" in captured.err
    # No send request claims a keyboard the account may not attach.
    sends = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMessageRequest)
    ]
    assert [getattr(item, "reply_markup", None) for item in sends] == [None]


def test_clone_sync_does_not_report_buttons_a_native_forward_keeps(
    config_env, monkeypatch, capsys
):
    """Only the rebuilding transports lose the keyboard; a forward is Telegram's
    own copy, so it is not counted as a loss."""
    seed_clone()
    client = CloneSyncClient([message(2, reply_markup=_inline_keyboard())])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    captured = capsys.readouterr()
    sync = json.loads(captured.out)["sync"]
    assert sync["forwarded"] == 1
    assert sync["markup_dropped"] == []
    assert "bot button(s)" not in captured.err


def test_clone_sync_reports_a_lost_keyboard_before_a_flood_ends_the_run(
    config_env, monkeypatch, capsys
):
    """A flood mid-run skips the result document, but the copy it already made
    is permanent and is never revisited — so the loss has to be on stderr by
    the time the run dies, not only in the tail (ADR-0085 review finding)."""
    clone_state = seed_clone()

    class FloodAfterFirstSend(CloneReuploadClient):
        async def __call__(self, request):
            if isinstance(request, functions.messages.SendMessageRequest) and any(
                isinstance(item, functions.messages.SendMessageRequest)
                for item in self.requests
            ):
                raise telethon_errors.FloodWaitError(request=request, capture=600)
            return await super().__call__(request)

    client = FloodAfterFirstSend(
        [
            message(2, message="with buttons", reply_markup=_inline_keyboard()),
            message(3, message="next"),
        ],
        protected=True,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5

    captured = capsys.readouterr()
    # No result document: the flood envelope is all the run gets to say.
    assert "sync" not in json.loads(captured.out)
    assert "source message 2" in captured.err
    assert "lost 2 bot button(s)" in captured.err
    # The copy that lost the keyboard is on the destination for good.
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_reports_a_lost_keyboard_on_the_snapshot_transport(
    config_env, monkeypatch, capsys
):
    """CONTRACT names reupload *and* snapshot: a poll is rendered as text, so
    whatever keyboard rode on it is gone the same way."""
    seed_clone()
    client = CloneReuploadClient(
        [message(2, media=poll_media(), reply_markup=_inline_keyboard())]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["snapshots"] == 1
    assert [row["id"] for row in sync["markup_dropped"]] == [2]


def test_clone_sync_reports_a_lost_keyboard_on_a_comment(
    config_env, monkeypatch, capsys
):
    """The comments leg copies through the same batch path; a bot keyboard on a
    discussion-group message is lost and counted exactly like a post's."""
    seed_comments_clone()
    anchored = anchor(10, 2)
    # Telegram mirrors a post's own keyboard onto the anchor; counting the
    # anchor would report one post's loss twice, once per leg.
    anchored.reply_markup = _inline_keyboard()
    client = CloneCommentsClient(
        [message(2)],
        [
            anchored,
            message(
                12,
                message="commented",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
                reply_markup=_inline_keyboard(),
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    # The anchor is skipped before any transport decides; only the comment
    # itself can contribute a row.
    assert [row["id"] for row in sync["markup_dropped"]] == [12]


def test_clone_sync_announces_every_lost_keyboard_not_only_the_first(
    config_env, monkeypatch, capsys
):
    """A run that dies before its result document leaves stderr as the whole
    record, so one line for the first loss would report 1 of N (review round
    two): every copy that loses a keyboard says so as it is made."""
    seed_clone()
    client = CloneReuploadClient(
        [
            message(2, message="first", reply_markup=_inline_keyboard()),
            message(3, message="second", reply_markup=_inline_keyboard()),
        ],
        protected=True,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    captured = capsys.readouterr()
    warnings = [line for line in captured.err.splitlines() if "bot button(s)" in line]
    assert len(warnings) == 2
    assert "source message 2" in warnings[0]
    assert "source message 3" in warnings[1]
    assert [
        row["id"] for row in json.loads(captured.out)["sync"]["markup_dropped"]
    ] == [
        2,
        3,
    ]
