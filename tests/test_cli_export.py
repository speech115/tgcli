import datetime as dt
import json

import pytest
from telethon import errors as telethon_errors

from tests.conftest import FakeClient, make_session_fake, ns
from tests.fakes.export import SAMPLE, make_export_fake, make_message
from tgcli.cli import main


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def test_export_messages_requires_output(config_env, capsys):
    assert main(["export", "messages", "@chan"]) == 1
    assert "--output" in capsys.readouterr().err


def test_export_messages_reuses_valid_existing_takeout(
    config_env, monkeypatch, tmp_path
):
    fake = make_export_fake(messages=[make_message(1, "message")])
    fake.session.takeout_id = 42
    make_session_fake(monkeypatch, fake)

    assert (
        main(
            [
                "export",
                "messages",
                "@chan",
                "--output",
                str(tmp_path / "messages.jsonl"),
            ]
        )
        == 0
    )
    assert fake.takeout_calls == [{}]


def test_export_messages_discards_malformed_takeout_id(
    config_env, monkeypatch, tmp_path
):
    fake = make_export_fake(messages=[make_message(1, "message")])
    fake.session.takeout_id = b""
    make_session_fake(monkeypatch, fake)

    assert (
        main(
            [
                "export",
                "messages",
                "@chan",
                "--output",
                str(tmp_path / "messages.jsonl"),
            ]
        )
        == 0
    )
    assert fake.session.takeout_id is None
    assert fake.takeout_calls == [{"chats": True, "megagroups": True, "channels": True}]


def test_export_messages_preserves_permalink_and_foreign_reply_peer(
    config_env, monkeypatch, tmp_path
):
    from telethon.tl import types

    entity = ns(id=4454061248, title="Channel", username="chan", broadcast=True)
    message = ns(
        id=2374,
        date=dt.datetime(2026, 7, 31, 10, 0, tzinfo=dt.UTC),
        sender_id=111,
        sender=ns(first_name="Alice", last_name=None),
        text="comment",
        media=None,
        reply_to_msg_id=1244,
        reply_to=types.MessageReplyHeader(
            reply_to_msg_id=1244,
            reply_to_peer_id=types.PeerChannel(2275285084),
            quote_text="foreign quote",
            reply_to_top_id=2373,
        ),
    )
    fake = FakeClient(messages=[message], entities={"@chan": entity})
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "messages.jsonl"

    assert main(["export", "messages", "@chan", "--output", str(destination)]) == 0

    exported = json.loads(destination.read_text(encoding="utf-8").splitlines()[0])
    assert exported["permalink"] == "https://t.me/chan/2374"
    assert exported["reply_to"] == {"id": 1244, "peer": -1002275285084}
    assert exported["quote_text"] == "foreign quote"


def test_export_messages_unknown_dialog_exits_4(
    config_env, monkeypatch, tmp_path, capsys
):
    make_session_fake(monkeypatch, make_export_fake())

    assert (
        main(
            [
                "--json",
                "export",
                "messages",
                "@ghost",
                "--output",
                str(tmp_path / "out.jsonl"),
            ]
        )
        == 4
    )
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "NOT_FOUND"


def test_export_messages_preserves_existing_destination_when_iteration_fails(
    config_env, monkeypatch, tmp_path, capsys
):
    fake = make_export_fake(messages=[make_message(1, "message")])
    fake.iter_messages_error = OSError("disk failed")
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "messages.jsonl"
    destination.write_text("previous\n")

    assert (
        main(
            [
                "--json",
                "export",
                "messages",
                "@chan",
                "--output",
                str(destination),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == captured.err
    assert json.loads(captured.out)["error"]["code"] == "RUNTIME"
    assert json.loads(captured.err)["error"]["code"] == "RUNTIME"
    assert destination.read_text() == "previous\n"


def test_takeout_delay_is_a_retryable_exit_5(config_env, monkeypatch, tmp_path, capsys):
    fake = make_export_fake()
    fake.takeout_error = telethon_errors.TakeoutInitDelayError(request=None, capture=90)
    make_session_fake(monkeypatch, fake)

    assert (
        main(
            [
                "--json",
                "export",
                "messages",
                "@chan",
                "--output",
                str(tmp_path / "out.jsonl"),
            ]
        )
        == 5
    )
    assert json.loads(capsys.readouterr().err)["error"] == {
        "code": "FLOOD_WAIT",
        "message": "takeout is unavailable for 90s; retry after 90s",
        "retry_after": 90,
    }


def test_export_takes_the_uniform_default_deadline(config_env, monkeypatch, tmp_path):
    """D2: export keeps no deadline exemption of its own."""
    fake = make_export_fake()
    make_session_fake(monkeypatch, fake)

    assert (
        main(["export", "messages", "@chan", "--output", str(tmp_path / "out.jsonl")])
        == 0
    )
