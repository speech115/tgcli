from pathlib import Path

import pytest

from tgcli.commands.media import (
    MediaSource,
    destination_for,
    parse_source,
    safe_filename,
)
from tgcli.errors import NotFoundError, PolicyError


def test_parse_source_accepts_public_link():
    assert parse_source("https://t.me/example_channel/42", None) == MediaSource(
        chat="@example_channel", message_id=42, private_channel_id=None
    )


def test_parse_source_accepts_private_link():
    assert parse_source("https://t.me/c/3817664407/878", None) == MediaSource(
        chat=None, message_id=878, private_channel_id=3817664407
    )


def test_parse_source_accepts_chat_and_message_id():
    assert parse_source("@channel", 42) == MediaSource(
        chat="@channel", message_id=42, private_channel_id=None
    )


def test_parse_source_rejects_incomplete_reference():
    with pytest.raises(NotFoundError, match="media source"):
        parse_source("@channel", None)


def test_safe_filename_cannot_escape_destination():
    assert safe_filename("../../a\tb.mp4", 42) == "a b.mp4"


def test_safe_filename_uses_message_id_when_name_is_empty():
    assert safe_filename("..", 42) == "media-42.bin"


def test_destination_refuses_existing_final_path(tmp_path):
    target = tmp_path / "already-there.bin"
    target.write_bytes(b"done")

    with pytest.raises(PolicyError, match="already exists"):
        destination_for("ignored.bin", str(target))


def test_destination_defaults_to_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert destination_for("file.bin", None) == tmp_path / "Downloads" / "file.bin"
