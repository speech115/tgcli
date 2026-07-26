import json
from datetime import UTC, datetime

import pytest

from tests.conftest import FakeClient, make_session_fake, ns
from tests.test_cli_dialogs import make_dialog
from tests.test_cli_media import _media_message
from tests.test_cli_read import make_read_client
from tests.test_cli_search import make_message
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


def test_batch_runs_ops_and_exits_zero(config_env, monkeypatch, capsys):
    client = FakeClient(
        dialogs=[make_dialog()],
        entities={
            "@alice": ns(
                id=111,
                first_name="A",
                last_name=None,
                username="alice",
                bot=False,
                contact=False,
            )
        },
    )
    make_session_fake(monkeypatch, client)
    stdin = (
        json.dumps({"op": "dialogs", "limit": 1})
        + "\n"
        + json.dumps({"op": "resolve", "ref": "@alice"})
        + "\n"
    )
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: stdin})())

    assert main(["batch"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 2
    assert lines[0]["ok"] is True and lines[0]["op"] == "dialogs"
    assert lines[1]["ok"] is True and lines[1]["op"] == "resolve"


def test_batch_nonzero_exit_on_partial_failure(config_env, monkeypatch, capsys):
    client = FakeClient(dialogs=[make_dialog()], entities={})
    make_session_fake(monkeypatch, client)
    stdin = (
        json.dumps({"op": "resolve", "ref": "@missing"})
        + "\n"
        + json.dumps({"op": "dialogs", "limit": 1})
        + "\n"
    )
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: stdin})())

    assert main(["batch"]) == 4
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["ok"] is False
    assert lines[1]["ok"] is True


def test_batch_fail_fast_stops_early(config_env, monkeypatch, capsys):
    client = FakeClient(entities={})
    make_session_fake(monkeypatch, client)
    stdin = (
        json.dumps({"op": "resolve", "ref": "@missing"})
        + "\n"
        + json.dumps({"op": "dialogs", "limit": 1})
        + "\n"
    )
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: stdin})())

    assert main(["batch", "--fail-fast"]) == 4
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 1
    assert lines[0]["ok"] is False


def test_batch_rejects_doctor_and_mutations(config_env, monkeypatch):
    make_session_fake(monkeypatch, FakeClient())
    for op in ("doctor", "send", "export", "media.download"):
        monkeypatch.setattr(
            "sys.stdin",
            type("S", (), {"read": lambda self, o=op: json.dumps({"op": o}) + "\n"})(),
        )
        assert main(["batch"]) == 2


def test_batch_rejects_over_100_ops(config_env, monkeypatch):
    make_session_fake(monkeypatch, FakeClient())
    lines = "\n".join(json.dumps({"op": "dialogs", "limit": 1}) for _ in range(101))
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: lines})())
    assert main(["batch"]) == 2


def test_batch_cap_counts_operations_not_blank_lines(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, FakeClient(dialogs=[]))
    ops = [json.dumps({"op": "dialogs", "limit": 1}) for _ in range(100)]
    lines = "\n".join([*ops[:50], "", *ops[50:]])
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: lines})())

    assert main(["batch"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 100


def test_batch_search_parses_iso_since(config_env, monkeypatch, capsys):
    client = FakeClient(
        search_messages={"needle": [make_message(42, "needle")]},
        entities={"@chan": ns(id=5, title="Chan")},
    )
    make_session_fake(monkeypatch, client)
    line = json.dumps(
        {
            "op": "search",
            "chat": "@chan",
            "query": "needle",
            "since": "2026-07-07T00:00:00+00:00",
        }
    )
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: line})())

    assert main(["batch"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["data"]["messages"] == []


def test_batch_read_parses_iso_date_bounds(config_env, monkeypatch, capsys):
    client = make_read_client()
    make_session_fake(monkeypatch, client)
    line = json.dumps(
        {
            "op": "read",
            "chat": "@chan",
            "since": "2026-07-17T00:00:00+00:00",
            "until": "2026-07-19T00:00:00+00:00",
        }
    )
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: line})())

    assert main(["batch"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert [item["id"] for item in result["data"]["messages"]] == [3, 2]


def test_batch_media_manifest_parses_iso_since(config_env, monkeypatch, capsys):
    client = FakeClient(
        messages=[
            _media_message(2, "photo", date=datetime(2026, 7, 22, tzinfo=UTC)),
            _media_message(1, "video", date=datetime(2026, 7, 18, tzinfo=UTC)),
        ],
        entities={"@chan": ns(id=5, title="Chan")},
    )
    make_session_fake(monkeypatch, client)
    line = json.dumps(
        {
            "op": "media.manifest",
            "source": "@chan",
            "since": "2026-07-20T00:00:00+00:00",
        }
    )
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: line})())

    assert main(["batch"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert [item["message_id"] for item in result["data"]["items"]] == [2]


@pytest.mark.parametrize("field, value", [("since", "2026-07-07"), ("from", "@alice")])
def test_batch_search_all_rejects_chat_scoped_filters(
    config_env, monkeypatch, capsys, field, value
):
    """--from/--since are chat-scoped; --all must fail, never silently drop them."""
    make_session_fake(monkeypatch, FakeClient())
    line = json.dumps({"op": "search", "query": "needle", "all": True, field: value})
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: line})())

    assert main(["batch"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["error"]["code"] == "BLOCKED"
    assert result["error"]["message"] == "search --all only supports QUERY and --limit"


def test_batch_dialogs_rejects_unknown_kind(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, FakeClient(dialogs=[make_dialog()]))
    line = json.dumps({"op": "dialogs", "kind": "chanel"})
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: line})())

    assert main(["batch"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["error"]["message"] == (
        "batch dialogs.kind must be one of: user, group, channel"
    )


def test_batch_media_manifest_rejects_unknown_type(config_env, monkeypatch, capsys):
    client = FakeClient(
        messages=[_media_message(2, "photo")],
        entities={"@chan": ns(id=5, title="Chan")},
    )
    make_session_fake(monkeypatch, client)
    line = json.dumps({"op": "media.manifest", "source": "@chan", "type": "foto"})
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: line})())

    assert main(["batch"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["error"]["message"] == (
        "batch media.manifest.type must be one of: photo, video, audio, voice, document"
    )


def test_batch_maps_flood_wait_to_exit_5(config_env, monkeypatch, capsys):
    from telethon import errors as telethon_errors

    client = FakeClient(dialogs=[make_dialog()])
    make_session_fake(monkeypatch, client)

    async def boom():
        raise telethon_errors.FloodWaitError(request=None, capture=3)
        yield

    client.iter_dialogs = boom
    monkeypatch.setattr(
        "sys.stdin",
        type(
            "S",
            (),
            {"read": lambda self: json.dumps({"op": "dialogs", "limit": 1}) + "\n"},
        )(),
    )
    assert main(["batch"]) == 5
    line = json.loads(capsys.readouterr().out.splitlines()[0])
    assert line["ok"] is False
    assert line["error"]["code"] == "FLOOD_WAIT"
    assert line["error"]["retry_after"] == 3
