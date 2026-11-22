"""Unit tests for poll/story snapshot rendering."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tgcli.clone import snapshot


def _poll_message(
    *,
    question: str = "Вопрос?",
    counts: list[tuple[str, int]],
    total_voters: int | None = None,
):
    answers = [
        types.PollAnswer(
            text=types.TextWithEntities(text=label, entities=[]),
            option=str(index).encode(),
        )
        for index, (label, _) in enumerate(counts)
    ]
    results = [
        types.PollAnswerVoters(option=str(index).encode(), voters=voters)
        for index, (_, voters) in enumerate(counts)
    ]
    total = (
        sum(voters for _, voters in counts) if total_voters is None else total_voters
    )
    media = types.MessageMediaPoll(
        poll=types.Poll(
            id=1,
            question=types.TextWithEntities(text=question, entities=[]),
            answers=answers,
            hash=7,
            public_voters=False,
            quiz=False,
            closed=False,
        ),
        results=types.PollResults(results=results, total_voters=total),
    )
    return SimpleNamespace(id=1, media=media)


@pytest.mark.asyncio
async def test_render_poll_snapshot_formats_progress_bars_and_percentages():
    full_text, full_entities, full_marker = await snapshot.render(
        None,
        _poll_message(counts=[("Все", 4), ("Никто", 0)]),
    )
    assert "📊 Результаты опроса" in full_text
    assert "Вопрос?" in full_text
    assert f"{'█' * 10} 100% · 4 голоса" in full_text
    assert f"{'░' * 10} 0% · 0 голосов" in full_text
    assert "Проголосовало: 4" in full_text
    assert full_entities == []
    assert full_marker is None

    split_text, split_entities, split_marker = await snapshot.render(
        None,
        _poll_message(counts=[("Да", 2), ("Нет", 2)]),
    )
    assert f"{'█' * 5}{'░' * 5} 50% · 2 голоса" in split_text
    assert split_entities == []
    assert split_marker is None

    skewed_text, skewed_entities, _ = await snapshot.render(
        None,
        _poll_message(counts=[("Да", 3), ("Нет", 1)]),
    )
    assert "75%" in skewed_text and "3 голоса" in skewed_text
    assert "25%" in skewed_text and "1 голос" in skewed_text
    assert skewed_entities == []


@pytest.mark.asyncio
async def test_render_poll_snapshot_formats_russian_vote_plurals():
    text, entities, marker = await snapshot.render(
        None,
        _poll_message(
            counts=[
                ("Один", 1),
                ("Два", 2),
                ("Пять", 5),
                ("Одиннадцать", 11),
                ("Двадцать один", 21),
            ]
        ),
    )
    assert "1 голос\n" in text
    assert "2 голоса\n" in text
    assert "5 голосов\n" in text
    assert "11 голосов\n" in text
    assert "21 голос\n" in text
    assert "Проголосовало: 40" in text
    assert entities == []
    assert marker is None


def test_story_render_links_known_username():
    class Client:
        async def get_entity(self, peer):
            return SimpleNamespace(
                title=None, first_name="Ann", last_name=None, username="ann"
            )

    media = types.MessageMediaStory(peer=types.PeerUser(user_id=7), id=3)
    message = SimpleNamespace(media=media)
    text, entities, marker = asyncio.run(snapshot.render(Client(), message))
    assert text == "Stories недоступна\nАвтор: Ann"
    assert len(entities) == 1
    assert entities[0].url == "https://t.me/ann"
    assert marker is None


def test_story_render_treats_private_peer_as_unknown_author():
    """Same refusal shape as attribution._resolve: ChannelPrivateError is an
    RPCError, not a ValueError — a missing author label, never a failed sync."""

    class Client:
        async def get_entity(self, peer):
            raise telethon_errors.ChannelPrivateError(request=None)

    media = types.MessageMediaStory(peer=types.PeerChannel(channel_id=9), id=3)
    message = SimpleNamespace(media=media)
    text, entities, marker = asyncio.run(snapshot.render(Client(), message))
    assert text == "Stories недоступна\nАвтор: неизвестен"
    assert entities == []
    assert marker is None


class VoteClient:
    def __init__(
        self,
        *,
        retract_ok=True,
        cast_updates=None,
        flood_on_retract=False,
    ):
        self.requests = []
        self.retract_ok = retract_ok
        self.flood_on_retract = flood_on_retract
        self.cast_updates = cast_updates

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.messages.SendVoteRequest):
            if request.options == []:
                if self.flood_on_retract:
                    raise telethon_errors.FloodWaitError(request=request, capture=30)
                if not self.retract_ok:
                    raise telethon_errors.RPCError(request, "RETRACT", 400)
                return SimpleNamespace(updates=[])
            if self.cast_updates is not None:
                return self.cast_updates
            return SimpleNamespace(
                updates=[
                    types.UpdateMessagePoll(
                        poll_id=1,
                        results=types.PollResults(
                            results=[
                                types.PollAnswerVoters(option=b"0", voters=5),
                                types.PollAnswerVoters(option=b"1", voters=6),
                            ],
                            total_voters=11,
                        ),
                    )
                ]
            )
        raise AssertionError(f"unexpected request {request!r}")


def _anonymous_open_poll_message(*, results=()):
    media = types.MessageMediaPoll(
        poll=types.Poll(
            id=1,
            question=types.TextWithEntities(text="Choose", entities=[]),
            answers=[
                types.PollAnswer(
                    text=types.TextWithEntities(text="First", entities=[]), option=b"0"
                ),
                types.PollAnswer(
                    text=types.TextWithEntities(text="Second", entities=[]), option=b"1"
                ),
            ],
            hash=0,
            public_voters=False,
            quiz=False,
            closed=False,
        ),
        results=types.PollResults(results=list(results), total_voters=10),
    )
    return SimpleNamespace(id=31, media=media)


@pytest.mark.asyncio
async def test_render_skips_vote_for_public_quiz_and_closed_polls():
    client = VoteClient()
    for kwargs in (
        {"public_voters": True},
        {"quiz": True},
        {"closed": True},
    ):
        media = types.MessageMediaPoll(
            poll=types.Poll(
                id=1,
                question=types.TextWithEntities(text="Q", entities=[]),
                answers=[
                    types.PollAnswer(
                        text=types.TextWithEntities(text="A", entities=[]), option=b"0"
                    )
                ],
                hash=0,
                **kwargs,
            ),
            results=types.PollResults(results=[], total_voters=3),
        )
        text, _, marker = await snapshot.render(
            client,
            SimpleNamespace(id=1, media=media),
            peer=object(),
            account_alias="main",
        )
        assert client.requests == []
        assert snapshot.BREAKDOWN_UNAVAILABLE in text
        assert marker == {"message_id": 1, "status": "skipped", "reason": "ineligible"}


@pytest.mark.asyncio
async def test_render_skips_vote_when_sends_blocked(monkeypatch):
    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    client = VoteClient()
    message = _anonymous_open_poll_message()
    text, _, marker = await snapshot.render(
        client, message, peer=object(), account_alias="main"
    )
    assert client.requests == []
    assert snapshot.BREAKDOWN_UNAVAILABLE in text
    assert marker["status"] == "skipped"
    assert marker["reason"] == "readonly"


@pytest.mark.asyncio
async def test_render_retract_failure_marks_loud_outcome(monkeypatch):
    notes = []
    monkeypatch.setattr(
        "tgcli.clone.snapshot.note",
        lambda msg: notes.append(msg),
    )
    monkeypatch.setattr(snapshot.safety, "append_audit", lambda *a, **k: None)

    client = VoteClient(retract_ok=False)
    message = _anonymous_open_poll_message()
    text, _, marker = await snapshot.render(
        client,
        message,
        peer=object(),
        account_alias="main",
    )
    assert "40% · 4 голоса" in text
    assert marker["status"] == "retract_failed"
    assert any("retract" in item.lower() for item in notes)


@pytest.mark.asyncio
async def test_render_retracts_even_when_cast_updates_lack_breakdown(monkeypatch):
    """Cast without UpdateMessagePoll still retracts and degrades."""
    notes = []
    audits = []
    monkeypatch.setattr(
        "tgcli.clone.snapshot.note",
        lambda msg: notes.append(msg),
    )
    monkeypatch.setattr(
        snapshot.safety,
        "append_audit",
        lambda kind, alias, payload: audits.append((kind, alias, payload)),
    )

    client = VoteClient(cast_updates=SimpleNamespace(updates=[]))
    message = _anonymous_open_poll_message()
    text, _, marker = await snapshot.render(
        client,
        message,
        peer=object(),
        account_alias="main",
    )
    votes = [
        req
        for req in client.requests
        if isinstance(req, functions.messages.SendVoteRequest)
    ]
    assert [req.options for req in votes] == [[b"0"], []]
    assert snapshot.BREAKDOWN_UNAVAILABLE in text
    assert marker["status"] == "capture_failed"
    assert [kind for kind, _, _ in audits] == [
        "clone-sync-poll-vote",
        "clone-sync-poll-retract",
        "clone-sync-poll-retract-result",
    ]
    assert audits[-1][2] == {"message_id": 31, "status": "retracted"}
    assert any("did not reveal" in item.lower() for item in notes)
