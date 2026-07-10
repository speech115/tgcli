import json

import pytest
from telethon import errors as telethon_errors
from telethon.tl import types

from tests.conftest import make_session_fake
from tgcli.cli import main


SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


class ApiClient:
    async def get_input_entity(self, value):
        assert value == "@self"
        return types.InputPeerSelf()

    async def __call__(self, request):
        assert request.__class__.__name__ == "GetFullUserRequest"

        class Result:
            def to_dict(self):
                return {"_": "UserFull", "id": 42}

        return Result()


def test_api_json_emits_raw_tl_envelope(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, ApiClient())

    assert main([
        "api", "users.getFullUser", "--params", '{"id":"@self"}', "--json"
    ]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "method": "users.getFullUser",
        "result": {"_": "UserFull", "id": 42},
    }


def test_api_floodwait_maps_to_exit_5(config_env, monkeypatch, capsys):
    class FloodClient(ApiClient):
        async def __call__(self, request):
            raise telethon_errors.FloodWaitError(request=None, capture=42)

    make_session_fake(monkeypatch, FloodClient())

    assert main([
        "api", "users.getFullUser", "--params", '{"id":"@self"}', "--json"
    ]) == 5

    assert json.loads(capsys.readouterr().err)["error"] == {
        "code": "FLOOD_WAIT",
        "message": "rate limited for 42s",
        "retry_after": 42,
    }


def test_api_write_is_blocked_without_opening_a_session(monkeypatch):
    from tgcli import cli

    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )

    assert main([
        "api", "messages.sendMessage", "--params", "{}", "--write"
    ]) == 2
