import json

import pytest

from tests.conftest import make_session_fake
from tests.test_cli_export import SAMPLE, make_export_fake, make_message
from tgcli.cli import main


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def test_export_messages_after_id_filters_min_id(
    config_env, monkeypatch, tmp_path, capsys
):
    fake = make_export_fake(
        messages=[
            make_message(3, "new"),
            make_message(2, "mid"),
            make_message(1, "old"),
        ]
    )
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "messages.jsonl"

    assert (
        main(
            [
                "--json",
                "export",
                "messages",
                "@chan",
                "--output",
                str(destination),
                "--after-id",
                "1",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["export"]["count"] == 2
    assert data["export"]["after_id"] == 1
    assert data["export"]["appended"] is False
    assert [
        json.loads(line)["id"] for line in destination.read_text().splitlines()
    ] == [2, 3]
    assert fake.iter_messages_kwargs["min_id"] == 1


def test_export_messages_append_requires_cursor(config_env, monkeypatch, tmp_path):
    fake = make_export_fake(messages=[make_message(1, "x")])
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "messages.jsonl"
    assert (
        main(
            [
                "export",
                "messages",
                "@chan",
                "--output",
                str(destination),
                "--append",
            ]
        )
        == 2
    )


def test_export_messages_append_with_after_id(
    config_env, monkeypatch, tmp_path, capsys
):
    fake = make_export_fake(
        messages=[make_message(3, "c"), make_message(2, "b"), make_message(1, "a")]
    )
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "messages.jsonl"
    destination.write_text(json.dumps({"id": 1, "text": "a"}) + "\n", encoding="utf-8")

    assert (
        main(
            [
                "--json",
                "export",
                "messages",
                "@chan",
                "--output",
                str(destination),
                "--append",
                "--after-id",
                "1",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["export"]["appended"] is True
    assert data["export"]["count"] == 2
    assert [
        json.loads(line)["id"] for line in destination.read_text().splitlines()
    ] == [1, 2, 3]


def test_export_messages_resume_from_last_line(
    config_env, monkeypatch, tmp_path, capsys
):
    fake = make_export_fake(
        messages=[make_message(3, "c"), make_message(2, "b"), make_message(1, "a")]
    )
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "messages.jsonl"
    destination.write_text(
        json.dumps({"id": 1}) + "\n" + json.dumps({"id": 2}) + "\n",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--json",
                "export",
                "messages",
                "@chan",
                "--output",
                str(destination),
                "--resume",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["export"]["after_id"] == 2
    assert data["export"]["appended"] is True
    assert data["export"]["count"] == 1
    assert [
        json.loads(line)["id"] for line in destination.read_text().splitlines()
    ] == [1, 2, 3]


def test_export_messages_resume_corrupt_exits_1(config_env, monkeypatch, tmp_path):
    fake = make_export_fake(messages=[make_message(1, "a")])
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "messages.jsonl"
    destination.write_text("{not-json\n", encoding="utf-8")
    assert (
        main(
            [
                "export",
                "messages",
                "@chan",
                "--output",
                str(destination),
                "--resume",
            ]
        )
        == 1
    )


def test_export_messages_resume_truncated_utf8_exits_1(
    config_env, monkeypatch, tmp_path, capsys
):
    fake = make_export_fake(messages=[make_message(1, "a")])
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "messages.jsonl"
    # Last line cut mid-way through a two-byte UTF-8 sequence.
    destination.write_bytes(b'{"id": 1, "text": "\xd0\xbf"}\n{"id": 2, "text": "\xd0')
    assert (
        main(
            [
                "export",
                "messages",
                "@chan",
                "--output",
                str(destination),
                "--resume",
            ]
        )
        == 1
    )
    assert "cannot resume" in capsys.readouterr().err


def test_export_messages_resume_missing_file_exits_1(config_env, monkeypatch, tmp_path):
    fake = make_export_fake(messages=[make_message(1, "a")])
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "missing.jsonl"
    assert (
        main(
            [
                "export",
                "messages",
                "@chan",
                "--output",
                str(destination),
                "--resume",
            ]
        )
        == 1
    )
