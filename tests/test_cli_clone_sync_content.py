"""clone sync: skipped, replaced and grouped content, lost keyboards."""

import json
from types import SimpleNamespace

import pytest
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tests.fakes.clone import (
    CloneReuploadClient,
    CloneSyncClient,
    message,
    poll_media,
    seed_clone,
)
from tgcli.cli import main
from tgcli.clone import state


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
