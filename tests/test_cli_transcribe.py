"""`tg transcribe` tests: boundary request, pending flow, refusals (ADR-0075)."""

import json

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import FakeClient, make_session_fake
from tgcli.cli import main
from tgcli.errors import CommandTimeoutError

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""

CHANNEL = types.Channel(
    id=3817664407,
    title="Socrates",
    photo=None,
    date=None,
    broadcast=True,
    access_hash=123,
)


def make_voice_message(message_id: int) -> types.Message:
    document = types.Document(
        id=1,
        access_hash=2,
        file_reference=b"ref",
        date=None,
        mime_type="audio/ogg",
        size=100,
        dc_id=1,
        attributes=[types.DocumentAttributeAudio(duration=3, voice=True)],
        thumbs=None,
    )
    return types.Message(
        id=message_id,
        peer_id=types.PeerChannel(channel_id=3817664407),
        media=types.MessageMediaDocument(document=document, alt_documents=None),
    )


def make_text_message(message_id: int) -> types.Message:
    return types.Message(
        id=message_id,
        peer_id=types.PeerChannel(channel_id=3817664407),
        message="hello",
    )


def transcribe_update(*, transcription_id=7, text="", pending=False):
    return types.UpdateTranscribedAudio(
        peer=types.PeerChannel(channel_id=3817664407),
        msg_id=42,
        transcription_id=transcription_id,
        text=text,
        pending=pending,
    )


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def test_transcribe_issues_exact_telethon_request(config_env, monkeypatch, capsys):
    client = FakeClient(
        entities={"@socrates": CHANNEL},
        messages=[make_voice_message(42)],
        transcribe_result=transcribe_update(
            transcription_id=7, text="Привет из голосового"
        ),
    )
    make_session_fake(monkeypatch, client)

    assert main(["--json", "transcribe", "@socrates", "42"]) == 0
    request = client.call_requests[-1]
    assert isinstance(request, functions.messages.TranscribeAudioRequest)
    assert request.msg_id == 42
    # The exact input peer type Telegram expects for a channel.
    assert isinstance(request.peer, types.InputPeerChannel)
    assert request.peer.channel_id == 3817664407
    assert request.peer.access_hash == 123

    data = json.loads(capsys.readouterr().out)
    assert data["message_id"] == 42
    assert data["dialog"] == {"id": 3817664407, "name": "Socrates"}
    assert data["transcription"] == {
        "text": "Привет из голосового",
        "transcription_id": 7,
        "pending": False,
    }


def test_transcribe_waits_for_async_update_when_pending(config_env, monkeypatch):
    import asyncio

    from tgcli.commands import transcribe as transcribe_cmd

    client = FakeClient(
        entities={"@socrates": CHANNEL},
        messages=[make_voice_message(42)],
        transcribe_result=transcribe_update(transcription_id=7, text="", pending=True),
    )
    make_session_fake(monkeypatch, client)

    async def scenario():
        task = asyncio.create_task(
            transcribe_cmd.transcribe_message(client, "@socrates", 42, timeout=5)
        )
        # Let the request be issued and the wait start, then deliver the
        # asynchronous update from the same event loop.
        await asyncio.sleep(0.01)
        client.fire_update(
            transcribe_update(transcription_id=7, text="Готово", pending=False)
        )
        return await task

    data = asyncio.run(scenario())
    assert data["transcription"] == {
        "text": "Готово",
        "transcription_id": 7,
        "pending": False,
    }
    # The raw handler is unregistered after the wait completes.
    assert client.event_handlers == []


def test_transcribe_replays_an_early_update_only_when_transcription_id_matches(
    config_env, monkeypatch
):
    """ADR-0075: an update parked before the RPC response must not settle the
    wait unless its transcription_id matches the response — the same msg_id
    from a concurrent transcription stays foreign (fails on the msg_id-only
    filter, where the early update settles the wait immediately)."""
    import asyncio

    from tgcli.commands import transcribe as transcribe_cmd

    client = FakeClient(
        entities={"@socrates": CHANNEL},
        messages=[make_voice_message(42)],
        transcribe_result=transcribe_update(transcription_id=7, text="", pending=True),
    )
    make_session_fake(monkeypatch, client)

    async def call_with_early_foreign_update(self, request):
        # The concurrent transcription's update lands before the response:
        # the handler can only park it; the replay must skip it (foreign id).
        self.fire_update(
            transcribe_update(transcription_id=99, text="Чужой", pending=False)
        )
        await asyncio.sleep(0)
        return self._transcribe_result

    monkeypatch.setattr(FakeClient, "__call__", call_with_early_foreign_update)

    async def scenario():
        task = asyncio.create_task(
            transcribe_cmd.transcribe_message(client, "@socrates", 42, timeout=5)
        )
        await asyncio.sleep(0.01)
        # The correct transcription lands after the response.
        client.fire_update(
            transcribe_update(transcription_id=7, text="Готово", pending=False)
        )
        return await task

    data = asyncio.run(scenario())
    assert data["transcription"] == {
        "text": "Готово",
        "transcription_id": 7,
        "pending": False,
    }
    assert client.event_handlers == []


def test_transcribe_ignores_update_with_foreign_transcription_id(
    config_env, monkeypatch
):
    """ADR-0075: the wait matches on transcription_id — an update for the same
    msg_id from a concurrent transcription must not satisfy it."""
    import asyncio

    from tgcli.commands import transcribe as transcribe_cmd

    client = FakeClient(
        entities={"@socrates": CHANNEL},
        messages=[make_voice_message(42)],
        transcribe_result=transcribe_update(transcription_id=7, text="", pending=True),
    )
    make_session_fake(monkeypatch, client)

    async def scenario():
        task = asyncio.create_task(
            transcribe_cmd.transcribe_message(client, "@socrates", 42, timeout=0.05)
        )
        await asyncio.sleep(0.01)
        # Same msg_id, different transcription_id: a concurrent transcription
        # of the same message (another dialog, or a re-run of this one).
        client.fire_update(
            transcribe_update(transcription_id=99, text="Чужой", pending=False)
        )
        with pytest.raises(CommandTimeoutError):
            await task

    asyncio.run(scenario())


def test_transcribe_rejects_non_voice_message(config_env, monkeypatch, capsys):
    client = FakeClient(
        entities={"@socrates": CHANNEL},
        messages=[make_text_message(42)],
    )
    make_session_fake(monkeypatch, client)

    assert main(["--json", "transcribe", "@socrates", "42"]) == 4
    assert "is not a voice message" in capsys.readouterr().err
    assert client.call_requests == []


def test_transcribe_reports_premium_refusal(config_env, monkeypatch, capsys):
    client = FakeClient(
        entities={"@socrates": CHANNEL},
        messages=[make_voice_message(42)],
    )
    make_session_fake(monkeypatch, client)

    async def raise_premium(self, request):
        raise telethon_errors.PremiumAccountRequiredError(
            request=functions.messages.TranscribeAudioRequest(
                peer=types.InputPeerChannel(channel_id=1, access_hash=1), msg_id=1
            )
        )

    monkeypatch.setattr(FakeClient, "__call__", raise_premium)

    assert main(["--json", "transcribe", "@socrates", "42"]) == 2
    assert "Premium" in capsys.readouterr().err


def test_transcribe_plain_timeout_reports_transcription_id(
    config_env, monkeypatch, capsys
):
    """CONTRACT §5: expiry reports the transcription_id — also in plain mode."""
    client = FakeClient(
        entities={"@socrates": CHANNEL},
        messages=[make_voice_message(42)],
        transcribe_result=transcribe_update(transcription_id=7, text="", pending=True),
    )
    make_session_fake(monkeypatch, client)

    assert main(["transcribe", "@socrates", "42", "--timeout", "0.05"]) == 1
    captured = capsys.readouterr()
    assert "transcription_id 7" in captured.err


def test_transcribe_times_out_when_update_never_arrives(
    config_env, monkeypatch, capsys
):
    client = FakeClient(
        entities={"@socrates": CHANNEL},
        messages=[make_voice_message(42)],
        transcribe_result=transcribe_update(transcription_id=7, text="", pending=True),
    )
    make_session_fake(monkeypatch, client)

    assert main(["--json", "transcribe", "@socrates", "42", "--timeout", "0.05"]) == 1
    captured = capsys.readouterr()
    # CONTRACT §5: expiry reports the transcription_id — in the JSON envelope
    # and on the plain stderr line.
    envelope = json.loads(captured.out)
    assert envelope["error"]["code"] == "TIMEOUT"
    assert envelope["error"]["transcription_id"] == 7
    assert "transcription_id 7" in captured.err
    assert client.event_handlers == []
