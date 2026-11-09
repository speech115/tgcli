"""A connection that dies mid-page still ends in exactly one error document.

`tg read` streams messages from Telethon's iterator. Seeded runs drop the
stream after a random number of messages with a random failure (FloodWait,
server error, Telegram timeout, a socket reset, a plain socket timeout, the
reconnect budget running out) and check what the caller sees: the documented
exit code, one JSON error envelope on stdout, no half page of messages and no
traceback. Everything runs on the FakeClient; nothing reaches Telegram.
"""

from __future__ import annotations

import json
import random

import pytest
from telethon import errors as telethon_errors
from telethon.tl import types

from tests.conftest import FakeClient, make_session_fake
from tests.fakes.messages import message
from tgcli.cli import main

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""

CHANNEL = types.Channel(
    id=1234,
    title="Chan",
    photo=types.ChatPhotoEmpty(),
    date=None,
    username="chan",
    broadcast=True,
)

FAILURES = (
    (
        "FLOOD_WAIT",
        5,
        lambda rng: telethon_errors.FloodWaitError(
            None, capture=rng.randint(1, 90_000)
        ),
    ),
    (
        "RUNTIME",
        1,
        lambda rng: telethon_errors.ServerError(None, message="INTERNAL", code=500),
    ),
    (
        "RUNTIME",
        1,
        lambda rng: telethon_errors.TimedOutError(None, message="TIMEOUT", code=-503),
    ),
    (
        "RUNTIME",
        1,
        lambda rng: ConnectionError("Connection to Telegram failed 5 time(s)"),
    ),
    ("RUNTIME", 1, lambda rng: ConnectionResetError(104, "Connection reset by peer")),
    ("RUNTIME", 1, lambda rng: TimeoutError("timed out")),
    ("RUNTIME", 1, lambda rng: OSError(101, "Network is unreachable")),
)


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def _client_that_drops(rng: random.Random):
    code, exit_code, make = FAILURES[rng.randrange(len(FAILURES))]
    delivered = rng.randint(0, 6)
    error = make(rng)
    page = [message(100 - index, CHANNEL) for index in range(delivered)]
    client = FakeClient(entities={"@chan": CHANNEL})

    async def dropping(entity, limit=None, **kwargs):
        for item in page:
            yield item
        raise error

    client.iter_messages = dropping
    return client, code, exit_code


@pytest.mark.parametrize("seed", range(28))
def test_a_stream_that_drops_mid_page_exits_with_one_error(
    seed, config_env, monkeypatch, capsys
):
    client, code, exit_code = _client_that_drops(random.Random(seed))
    make_session_fake(monkeypatch, client)

    assert main(["--json", "read", "@chan", "--limit", "20"]) == exit_code

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1, "exactly one JSON document on stdout"
    document = json.loads(lines[0])
    assert set(document) == {"error"}, "no partial page next to the error"
    assert document["error"]["code"] == code
    if code == "FLOOD_WAIT":
        assert document["error"]["retry_after"] > 0
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("seed", range(8))
def test_a_drop_in_plain_mode_prints_no_partial_rows(
    seed, config_env, monkeypatch, capsys
):
    client, _, exit_code = _client_that_drops(random.Random(1000 + seed))
    make_session_fake(monkeypatch, client)

    assert main(["--plain", "read", "@chan"]) == exit_code

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err
