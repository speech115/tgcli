import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from telethon import errors as telethon_errors

from tests.conftest import FakeClient, make_session_fake, ns
from tests.fakes.media import (
    StreamingClient,
    media_message,
    photo_media,
    real_message,
)
from tgcli import session
from tgcli.cli import main
from tgcli.commands import media as media_cmd
from tgcli.errors import ConfigError, NotFoundError

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


def test_media_download_message_ids_bulk(config_env, monkeypatch, tmp_path, capsys):
    make_session_fake(monkeypatch, FakeClient(entities={"@chan": ns(id=5, title="C")}))
    calls = []

    async def fake_download(tg, source, account_alias, **kwargs):
        calls.append(source.message_id)
        path = tmp_path / f"{source.message_id}.bin"
        path.write_bytes(b"x")
        return {
            "source": f"@chan:{source.message_id}",
            "path": str(path),
            "bytes": 1,
            "resumed": False,
            "parallel": 1,
        }

    async def fake_resolve(tg, source, account_alias):
        return ns(id=5), media_cmd._DownloadTarget(
            media=None, filename=f"{source.message_id}.bin", size=None
        )

    monkeypatch.setattr(media_cmd, "download_media", fake_download)
    monkeypatch.setattr(media_cmd, "resolve_message", fake_resolve)

    assert (
        main(
            [
                "--json",
                "media",
                "download",
                "@chan",
                "--message-ids",
                "10,11",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 2
    assert data["failed"] == []
    assert calls == [10, 11]


def test_media_download_bulk_skip_only_keeps_exit_zero_and_emits_skipped_json(
    config_env, monkeypatch, tmp_path, capsys
):
    make_session_fake(monkeypatch, FakeClient(entities={"@chan": ns(id=5, title="C")}))

    async def fake_download(tg, source, account_alias, **kwargs):
        raise media_cmd.PolicyError(
            f"output path already exists: {tmp_path / f'{source.message_id}.bin'}"
        )

    async def fake_resolve(tg, source, account_alias):
        return ns(id=5), media_cmd._DownloadTarget(
            media=None, filename=f"{source.message_id}.bin", size=None
        )

    monkeypatch.setattr(media_cmd, "download_media", fake_download)
    monkeypatch.setattr(media_cmd, "resolve_message", fake_resolve)

    assert (
        main(
            [
                "--json",
                "media",
                "download",
                "@chan",
                "--message-ids",
                "10,11",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 0
    assert data["items"] == []
    assert data["failed"] == []
    assert data["skipped"] == [
        {
            "message_id": 10,
            "reason": f"output path already exists: {tmp_path / '10.bin'}",
        },
        {
            "message_id": 11,
            "reason": f"output path already exists: {tmp_path / '11.bin'}",
        },
    ]


def test_media_download_bulk_failed_nonzero_exit(
    config_env, monkeypatch, tmp_path, capsys
):
    make_session_fake(monkeypatch, FakeClient(entities={"@chan": ns(id=5, title="C")}))

    async def fake_download(tg, source, account_alias, **kwargs):
        if source.message_id == 11:
            raise NotFoundError("missing")
        path = tmp_path / f"{source.message_id}.bin"
        path.write_bytes(b"x")
        return {
            "source": f"@chan:{source.message_id}",
            "path": str(path),
            "bytes": 1,
            "resumed": False,
            "parallel": 1,
        }

    async def fake_resolve(tg, source, account_alias):
        return ns(id=5), media_cmd._DownloadTarget(
            media=None, filename=f"{source.message_id}.bin", size=None
        )

    monkeypatch.setattr(media_cmd, "download_media", fake_download)
    monkeypatch.setattr(media_cmd, "resolve_message", fake_resolve)

    assert (
        main(
            [
                "--json",
                "media",
                "download",
                "@chan",
                "--message-ids",
                "10,11",
                "--output",
                str(tmp_path),
            ]
        )
        == 4
    )
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 1
    assert len(data["failed"]) == 1
    assert data["failed"][0]["message_id"] == 11


def test_media_download_missing_explicit_id_continues_without_filters(
    config_env, monkeypatch, tmp_path, capsys
):
    """Candidate resolution NotFound must become failed, not a top-level abort."""
    make_session_fake(
        monkeypatch,
        FakeClient(
            messages=[media_message(11, "photo")],
            entities={"@chan": ns(id=5, title="C")},
        ),
    )
    downloaded = []

    async def fake_download(tg, source, account_alias, **kwargs):
        downloaded.append(source.message_id)
        path = tmp_path / f"{source.message_id}.bin"
        path.write_bytes(b"x")
        return {
            "source": f"@chan:{source.message_id}",
            "path": str(path),
            "bytes": 1,
            "resumed": False,
            "parallel": 1,
        }

    monkeypatch.setattr(media_cmd, "download_media", fake_download)

    assert (
        main(
            [
                "--json",
                "media",
                "download",
                "@chan",
                "--message-ids",
                "10,11",
                "--output",
                str(tmp_path),
            ]
        )
        == 4
    )
    data = json.loads(capsys.readouterr().out)
    assert downloaded == [11]
    assert [item["message_id"] for item in data["items"]] == [11]
    assert data["failed"] == [
        {"message_id": 10, "error": "downloadable media not found: 10"}
    ]


def test_media_download_missing_explicit_id_continues_with_filters(
    config_env, monkeypatch, tmp_path, capsys
):
    make_session_fake(
        monkeypatch,
        FakeClient(
            messages=[media_message(11, "video")],
            entities={"@chan": ns(id=5, title="C")},
        ),
    )
    downloaded = []

    async def fake_download(tg, source, account_alias, **kwargs):
        downloaded.append(source.message_id)
        path = tmp_path / f"{source.message_id}.bin"
        path.write_bytes(b"x")
        return {
            "source": f"@chan:{source.message_id}",
            "path": str(path),
            "bytes": 1,
            "resumed": False,
            "parallel": 1,
        }

    monkeypatch.setattr(media_cmd, "download_media", fake_download)

    assert (
        main(
            [
                "--json",
                "media",
                "download",
                "@chan",
                "--message-ids",
                "10,11",
                "--type",
                "video",
                "--output",
                str(tmp_path),
            ]
        )
        == 4
    )
    data = json.loads(capsys.readouterr().out)
    assert downloaded == [11]
    assert [item["message_id"] for item in data["items"]] == [11]
    assert data["failed"] == [{"message_id": 10, "error": "message not found: 10"}]


def _bulk_download_fake(tmp_path, missing=()):
    async def fake_download(tg, source, account_alias, **kwargs):
        if source.message_id in missing:
            raise NotFoundError("missing")
        path = tmp_path / f"{source.message_id}.bin"
        path.write_bytes(b"x")
        return {
            "source": f"@chan:{source.message_id}",
            "path": str(path),
            "bytes": 1,
            "resumed": False,
            "parallel": 1,
        }

    return fake_download


async def _fake_resolve(tg, source, account_alias):
    return ns(id=5), media_cmd._DownloadTarget(
        media=None, filename=f"{source.message_id}.bin", size=None
    )


def test_media_download_bulk_plain_rows_match_the_frozen_columns(
    config_env, monkeypatch, tmp_path, capsys
):
    """CONTRACT freezes `media download` rows as path, bytes, resumed, parallel."""
    make_session_fake(monkeypatch, FakeClient(entities={"@chan": ns(id=5, title="C")}))
    monkeypatch.setattr(media_cmd, "download_media", _bulk_download_fake(tmp_path))
    monkeypatch.setattr(media_cmd, "resolve_message", _fake_resolve)

    assert (
        main(
            [
                "--plain",
                "media",
                "download",
                "@chan",
                "--message-ids",
                "10,11",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.splitlines() == [
        f"{tmp_path / '10.bin'}\t1\tFalse\t1",
        f"{tmp_path / '11.bin'}\t1\tFalse\t1",
    ]


def test_media_download_bulk_plain_keeps_successes_on_partial_failure(
    config_env, monkeypatch, tmp_path, capsys
):
    make_session_fake(monkeypatch, FakeClient(entities={"@chan": ns(id=5, title="C")}))
    monkeypatch.setattr(
        media_cmd, "download_media", _bulk_download_fake(tmp_path, missing={11})
    )
    monkeypatch.setattr(media_cmd, "resolve_message", _fake_resolve)

    assert (
        main(
            [
                "--plain",
                "media",
                "download",
                "@chan",
                "--message-ids",
                "10,11",
                "--output",
                str(tmp_path),
            ]
        )
        == 4
    )
    assert capsys.readouterr().out.splitlines() == [
        f"{tmp_path / '10.bin'}\t1\tFalse\t1"
    ]


def test_media_download_rejects_over_100_ids(config_env, monkeypatch):
    make_session_fake(monkeypatch, FakeClient(entities={"@chan": ns(id=5, title="C")}))
    ids = ",".join(str(i) for i in range(1, 102))
    assert main(["media", "download", "@chan", "--message-ids", ids]) == 2


def test_media_download_rejects_codec_in_bulk_mode(config_env, monkeypatch, capsys):
    """ADR-0078: --codec is story-only; bulk mode must not silently ignore it."""
    make_session_fake(monkeypatch, FakeClient())

    assert (
        main(
            [
                "--json",
                "media",
                "download",
                "@chan",
                "--message-ids",
                "1,2",
                "--codec",
                "h264",
            ]
        )
        == 2
    )
    assert "story sources only" in capsys.readouterr().err


def test_media_download_rejects_message_id_with_bulk_flags(config_env, monkeypatch):
    make_session_fake(monkeypatch, FakeClient())
    assert main(["media", "download", "@chan", "42", "--message-ids", "1,2"]) == 2


def test_media_download_message_ids_combine_with_filters_and_limit(
    config_env, monkeypatch, tmp_path, capsys
):
    messages = {
        10: media_message(10, "photo", date=datetime(2026, 7, 22, tzinfo=UTC)),
        11: media_message(11, "video", date=datetime(2026, 7, 18, tzinfo=UTC)),
        12: media_message(12, "video", date=datetime(2026, 7, 22, tzinfo=UTC)),
        13: media_message(13, "video", date=datetime(2026, 7, 23, tzinfo=UTC)),
    }
    make_session_fake(
        monkeypatch,
        FakeClient(
            messages=list(messages.values()),
            entities={"@chan": ns(id=5, title="C")},
        ),
    )
    downloaded = []

    async def fake_download(tg, source, account_alias, **kwargs):
        downloaded.append(source.message_id)
        path = tmp_path / f"{source.message_id}.bin"
        path.write_bytes(b"x")
        return {
            "source": f"@chan:{source.message_id}",
            "path": str(path),
            "bytes": 1,
            "resumed": False,
            "parallel": 1,
        }

    monkeypatch.setattr(media_cmd, "download_media", fake_download)

    assert (
        main(
            [
                "--json",
                "media",
                "download",
                "@chan",
                "--message-ids",
                "10,11,12,13",
                "--type",
                "video",
                "--since",
                "2026-07-20T00:00:00+00:00",
                "--limit",
                "1",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert downloaded == [12]
    assert [item["message_id"] for item in data["items"]] == [12]


def test_media_download_filter_limit_continues_past_skipped_existing_files(
    config_env, monkeypatch, tmp_path, capsys
):
    messages = [
        media_message(10, "video_note", date=datetime(2026, 7, 24, tzinfo=UTC)),
        media_message(11, "video_note", date=datetime(2026, 7, 23, tzinfo=UTC)),
        media_message(12, "video_note", date=datetime(2026, 7, 22, tzinfo=UTC)),
        media_message(13, "video_note", date=datetime(2026, 7, 21, tzinfo=UTC)),
    ]
    make_session_fake(
        monkeypatch,
        FakeClient(messages=messages, entities={"@chan": ns(id=5, title="C")}),
    )
    downloaded = []

    async def fake_download(tg, source, account_alias, **kwargs):
        target = tmp_path / f"{source.message_id}.bin"
        if source.message_id in {10, 11}:
            raise media_cmd.PolicyError(f"output path already exists: {target}")
        downloaded.append(source.message_id)
        target.write_bytes(b"x")
        return {
            "source": f"@chan:{source.message_id}",
            "path": str(target),
            "bytes": 1,
            "resumed": False,
            "parallel": 1,
        }

    monkeypatch.setattr(media_cmd, "download_media", fake_download)

    assert (
        main(
            [
                "--json",
                "media",
                "download",
                "@chan",
                "--type",
                "video_note",
                "--limit",
                "2",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert downloaded == [12, 13]
    assert [item["message_id"] for item in data["items"]] == [12, 13]
    assert [item["message_id"] for item in data["skipped"]] == [10, 11]
    assert data["count"] == 2


def test_media_download_type_filter_skips_text_only_explicit_id(
    config_env, monkeypatch, tmp_path, capsys
):
    text = media_message(10, "photo", date=datetime(2026, 7, 22, tzinfo=UTC))
    text.media = None
    text.file = None
    video = media_message(11, "video", date=datetime(2026, 7, 22, tzinfo=UTC))
    client = FakeClient(
        messages=[text, video],
        entities={"@chan": ns(id=5, title="C")},
    )
    make_session_fake(monkeypatch, client)

    async def fake_download(tg, source, account_alias, **kwargs):
        path = tmp_path / f"{source.message_id}.bin"
        path.write_bytes(b"x")
        return {
            "source": f"@chan:{source.message_id}",
            "path": str(path),
            "bytes": 1,
            "resumed": False,
            "parallel": 1,
        }

    monkeypatch.setattr(media_cmd, "download_media", fake_download)

    assert (
        main(
            [
                "--json",
                "media",
                "download",
                "@chan",
                "--message-ids",
                "10,11",
                "--type",
                "video",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["failed"] == []
    assert [item["message_id"] for item in data["items"]] == [11]


@pytest.mark.parametrize(
    "error_type",
    [
        telethon_errors.SessionRevokedError,
        telethon_errors.AuthKeyUnregisteredError,
        telethon_errors.SessionExpiredError,
        telethon_errors.UserDeactivatedError,
    ],
)
def test_media_download_session_revoked_keeps_auth_exit_code(
    config_env, monkeypatch, tmp_path, capsys, error_type
):
    """A dead session is the run's problem, not one item's: bulk download
    re-raises it so `session.client` maps it to exit 3, never `failed[]`."""
    client = FakeClient(entities={"@chan": ns(id=5, title="C")})

    @asynccontextmanager
    async def fake_session(account, *, mutation_safe=False, role=None, govern=True):
        try:
            yield client
        except telethon_errors.UnauthorizedError as exc:
            raise ConfigError("session needs reauthentication") from exc

    async def fake_resolve(tg, source, account_alias):
        return ns(id=5), media_cmd._DownloadTarget(
            media=media_message(source.message_id, "video"),
            filename=f"{source.message_id}.bin",
            size=10,
        )

    async def revoked_download(tg, source, account_alias, **kwargs):
        raise error_type(request=None)

    monkeypatch.setattr(session, "client", fake_session)
    monkeypatch.setattr(media_cmd, "resolve_message", fake_resolve)
    monkeypatch.setattr(media_cmd, "download_media", revoked_download)

    assert (
        main(
            [
                "--json",
                "media",
                "download",
                "@chan",
                "--message-ids",
                "10",
                "--output",
                str(tmp_path),
            ]
        )
        == 3
    )
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "CONFIG"


class _BytesClient(FakeClient):
    """Streams each message's own bytes, keyed by its media object."""

    def __init__(self, payloads, **kwargs):
        super().__init__(**kwargs)
        self._payloads = payloads

    async def iter_download(self, media, *, offset=0, request_size=None, **kwargs):
        yield self._payloads[id(media)][offset:]


def test_media_download_bulk_keeps_two_files_with_the_same_name(
    config_env, monkeypatch, tmp_path, capsys
):
    first = media_message(10, "document", filename="report.pdf", size=3)
    second = media_message(11, "document", filename="report.pdf", size=3)
    client = _BytesClient(
        {id(first.media): b"one", id(second.media): b"two"},
        messages=[first, second],
        entities={"@chan": ns(id=5, title="C")},
    )
    make_session_fake(monkeypatch, client)
    out = tmp_path / "out"

    argv = ["--json", "media", "download", "@chan", "--message-ids", "10,11"]
    assert main([*argv, "--output", str(out)]) == 0
    data = json.loads(capsys.readouterr().out)

    assert data["skipped"] == []
    assert [(item["message_id"], item["path"]) for item in data["items"]] == [
        (10, str(out / "10-report.pdf")),
        (11, str(out / "11-report.pdf")),
    ]
    assert (out / "10-report.pdf").read_bytes() == b"one"
    assert (out / "11-report.pdf").read_bytes() == b"two"

    assert main([*argv, "--output", str(out)]) == 0
    rerun = json.loads(capsys.readouterr().out)
    assert rerun["items"] == []
    assert [item["message_id"] for item in rerun["skipped"]] == [10, 11]


def test_media_download_bulk_resumes_a_checkpoint_from_before_the_id_prefix(
    config_env, monkeypatch, tmp_path, capsys
):
    message = media_message(10, "document", filename="report.pdf", size=3)
    client = _BytesClient(
        {id(message.media): b"one"},
        messages=[message],
        entities={"@chan": ns(id=5, title="C")},
    )
    make_session_fake(monkeypatch, client)
    out = tmp_path / "out"
    # A bulk run from before the prefix was interrupted after one byte.
    source = media_cmd.MediaSource(chat="@chan", message_id=10, private_channel_id=None)
    state_path, part_path = media_cmd._state_paths(source)
    part_path.parent.mkdir(parents=True)
    part_path.write_bytes(b"o")
    target = media_cmd._DownloadTarget(
        media=message.media, filename="report.pdf", size=3
    )
    media_cmd._write_state(state_path, source, out / "report.pdf", target, 1)

    argv = ["--json", "media", "download", "@chan", "--message-ids", "10"]
    assert main([*argv, "--output", str(out)]) == 0
    data = json.loads(capsys.readouterr().out)

    assert data["items"] == [
        {
            "message_id": 10,
            "path": str(out / "10-report.pdf"),
            "bytes": 3,
            "resumed": True,
        }
    ]
    assert (out / "10-report.pdf").read_bytes() == b"one"


def test_media_download_bulk_resumes_an_unprefixed_bin_checkpoint(
    config_env, monkeypatch, tmp_path, capsys
):
    media = photo_media()
    client = StreamingClient(
        messages=[real_message(42, media)],
        entities={"@chan": ns(id=5, title="C")},
    )
    make_session_fake(monkeypatch, client)
    out = tmp_path / "out"
    # An unnamed photo from before both the id prefix and the typed extension.
    source = media_cmd.MediaSource(chat="@chan", message_id=42, private_channel_id=None)
    state_path, part_path = media_cmd._state_paths(source)
    part_path.parent.mkdir(parents=True)
    part_path.write_bytes(b"a")
    target = media_cmd._DownloadTarget(media=media, filename="media-42.bin", size=3)
    media_cmd._write_state(state_path, source, out / "media-42.bin", target, 1)

    argv = ["--json", "media", "download", "@chan", "--message-ids", "42"]
    assert main([*argv, "--output", str(out)]) == 0
    data = json.loads(capsys.readouterr().out)

    assert data["items"] == [
        {
            "message_id": 42,
            "path": str(out / "42-media-42.jpg"),
            "bytes": 3,
            "resumed": True,
        }
    ]
    assert (out / "42-media-42.jpg").read_bytes() == b"abc"
