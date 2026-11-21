"""A run cut short between Telegram's send and the mapping save (L2)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from telethon.tl import functions

from tests.conftest import make_session_fake
from tests.fakes.clone import CloneSyncClient, message, seed_clone
from tests.fakes.clone_env import config_env as config_env
from tgcli.cli import main
from tgcli.clone import state


class FirstForwardFails(CloneSyncClient):
    """The first forward either lands without an answer, or never leaves."""

    def __init__(self, messages, *, lands: bool):
        super().__init__(messages)
        self.lands = lands
        self.failed = False

    async def __call__(self, request):
        if (
            isinstance(request, functions.messages.ForwardMessagesRequest)
            and not self.failed
        ):
            self.failed = True
            if not self.lands:
                raise ConnectionError("dropped before the send")
            await super().__call__(request)
            return SimpleNamespace(updates=[])
        return await super().__call__(request)


def _forwarded(client) -> list[list[int]]:
    return [
        list(request.id)
        for request in client.requests
        if isinstance(request, functions.messages.ForwardMessagesRequest)
    ]


def test_next_run_adopts_a_batch_telegram_sent_but_did_not_confirm(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = FirstForwardFails([message(2), message(3)], lands=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2
    assert "did not confirm" in capsys.readouterr().err
    interrupted = state.load(clone_state.clone_id)
    assert interrupted is not None
    assert interrupted.send_intents == {"posts": {"kind": "batch", "sources": [2]}}

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert "adopted 1 message" in capsys.readouterr().err
    resumed = state.load(clone_state.clone_id)
    assert resumed is not None
    assert resumed.id_map == {2: 2, 3: 3}
    assert resumed.send_intents == {}
    assert _forwarded(client) == [[2], [3]]


def test_a_send_that_never_left_is_sent_again(config_env, monkeypatch, capsys):
    clone_state = seed_clone()
    client = FirstForwardFails([message(2)], lands=False)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) != 0
    capsys.readouterr()

    assert main(["clone", "sync", "@source", "--json"]) == 0

    resumed = state.load(clone_state.clone_id)
    assert resumed is not None
    assert resumed.id_map == {2: 2}
    assert resumed.send_intents == {}
    assert client.failed and _forwarded(client) == [[2]]


def test_a_tail_the_intent_does_not_explain_still_needs_repair(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = FirstForwardFails([message(2), message(3)], lands=True)
    make_session_fake(monkeypatch, client)
    assert main(["clone", "sync", "@source", "--json"]) == 2
    capsys.readouterr()
    client.destination_last_id += 1

    assert main(["clone", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)["error"]
    assert "unexpected tail" in error["message"]
    assert error["unexpected"] == 2
    kept = state.load(clone_state.clone_id)
    assert kept is not None
    assert kept.id_map == {}
    assert kept.send_intents == {"posts": {"kind": "batch", "sources": [2]}}
    assert _forwarded(client) == [[2]]
