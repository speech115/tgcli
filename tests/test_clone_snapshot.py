"""Unit tests for poll/story snapshot rendering (ADR-0019 / ADR-0048)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tgcli.clone import snapshot


def test_bar_renders_eighth_blocks():
    assert snapshot._bar(0) == "░" * 10
    assert snapshot._bar(100) == "█" * 10
    assert snapshot._bar(50) == "█████" + "░" * 5


def test_vote_word_russian_plurals():
    assert snapshot._vote_word(1) == "голос"
    assert snapshot._vote_word(2) == "голоса"
    assert snapshot._vote_word(5) == "голосов"
    assert snapshot._vote_word(11) == "голосов"
    assert snapshot._vote_word(21) == "голос"


def _poll_media(
    *,
    total_voters=4,
    results=None,
    public_voters=False,
    quiz=False,
    closed=False,
    answers=None,
):
    if answers is None:
        answers = [
            SimpleNamespace(text=SimpleNamespace(text="Да"), option=b"0"),
            SimpleNamespace(text=SimpleNamespace(text="Нет"), option=b"1"),
        ]
    if results is None:
        results = [
            SimpleNamespace(option=b"0", voters=3),
            SimpleNamespace(option=b"1", voters=1),
        ]
    return SimpleNamespace(
        poll=SimpleNamespace(
            question=SimpleNamespace(text="Вопрос?"),
            answers=answers,
            public_voters=public_voters,
            quiz=quiz,
            closed=closed,
            hash=7,
        ),
        results=SimpleNamespace(total_voters=total_voters, results=results),
    )


def test_poll_snapshot_text():
    text, entities = snapshot._poll_snapshot(_poll_media())
    assert "📊 Результаты опроса" in text
    assert "Вопрос?" in text
    assert "75%" in text and "3 голоса" in text
    assert "Проголосовало: 4" in text
    assert entities == []


def test_poll_snapshot_unavailable_breakdown_when_voters_without_results():
    media = _poll_media(total_voters=10, results=[])
    text, entities = snapshot._poll_snapshot(media)
    assert "Вопрос?" in text
    assert snapshot.BREAKDOWN_UNAVAILABLE in text
    assert "0% · 0" not in text
    assert "Проголосовало: 10" in text
    assert entities == []


def test_poll_snapshot_subtracts_own_vote_counts():
    media = _poll_media(
        total_voters=11,
        results=[
            SimpleNamespace(option=b"0", voters=4),
            SimpleNamespace(option=b"1", voters=7),
        ],
    )
    text, _ = snapshot._poll_snapshot(media, chosen_option=b"0")
    assert "30% · 3 голоса" in text  # 4-1
    assert "70% · 7 голосов" in text
    assert "Проголосовало: 10" in text  # 11-1


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
async def test_render_casts_and_retracts_vote_for_anonymous_open_poll(monkeypatch):
    audits = []
    monkeypatch.setattr(
        snapshot.safety,
        "append_audit",
        lambda kind, alias, payload: audits.append((kind, alias, payload)),
    )

    async def invoke(make_awaitable):
        return await make_awaitable()

    client = VoteClient()
    message = _anonymous_open_poll_message()
    text, _, marker = await snapshot.render(
        client,
        message,
        peer=object(),
        account_alias="main",
        invoke=invoke,
    )
    votes = [
        req
        for req in client.requests
        if isinstance(req, functions.messages.SendVoteRequest)
    ]
    assert len(votes) == 2
    assert votes[0].options == [b"0"]
    assert votes[0].msg_id == 31
    assert votes[1].options == []
    assert "40% · 4 голоса" in text  # 5-1
    assert "Проголосовало: 10" in text
    assert marker == {"message_id": 31, "status": "captured"}
    assert [kind for kind, _, _ in audits] == [
        "clone-sync-poll-vote",
        "clone-sync-poll-retract",
        "clone-sync-poll-retract-result",
    ]
    assert audits[-1][2] == {"message_id": 31, "status": "retracted"}


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

    async def invoke(make_awaitable):
        return await make_awaitable()

    client = VoteClient(retract_ok=False)
    message = _anonymous_open_poll_message()
    text, _, marker = await snapshot.render(
        client,
        message,
        peer=object(),
        account_alias="main",
        invoke=invoke,
    )
    assert "40% · 4 голоса" in text
    assert marker["status"] == "retract_failed"
    assert any("retract" in item.lower() for item in notes)


@pytest.mark.asyncio
async def test_render_retracts_even_when_cast_updates_lack_breakdown(monkeypatch):
    """Cast without UpdateMessagePoll still retracts and degrades (ADR-0048)."""
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

    async def invoke(make_awaitable):
        return await make_awaitable()

    client = VoteClient(cast_updates=SimpleNamespace(updates=[]))
    message = _anonymous_open_poll_message()
    text, _, marker = await snapshot.render(
        client,
        message,
        peer=object(),
        account_alias="main",
        invoke=invoke,
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


@pytest.mark.asyncio
async def test_render_retract_flood_wait_is_disclosed_not_propagated(monkeypatch):
    """A FloodWait on the retract leaves a standing vote — it must be disclosed.

    Propagating it would kill the run before the poll_votes tail, so ADR-0048
    decisions 2/4 would be broken silently: the next run sees a breakdown
    (the vote is still cast) and never retries the retract.
    """
    notes = []
    audits = []
    monkeypatch.setattr("tgcli.clone.snapshot.note", lambda msg: notes.append(msg))
    monkeypatch.setattr(
        snapshot.safety,
        "append_audit",
        lambda kind, alias, payload: audits.append((kind, alias, payload)),
    )

    async def invoke(make_awaitable):
        return await make_awaitable()

    client = VoteClient(flood_on_retract=True)
    message = _anonymous_open_poll_message()
    text, _, marker = await snapshot.render(
        client,
        message,
        peer=object(),
        account_alias="main",
        invoke=invoke,
    )
    assert "40% · 4 голоса" in text
    assert marker["message_id"] == 31
    assert marker["status"] == "retract_failed"
    assert "30 seconds" in marker["error"]
    assert any("retract" in item.lower() and "31" in item for item in notes)
    assert [kind for kind, _, _ in audits] == [
        "clone-sync-poll-vote",
        "clone-sync-poll-retract",
        "clone-sync-poll-retract-result",
    ]
    assert audits[-1][2]["status"] == "failed"
