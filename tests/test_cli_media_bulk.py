import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from telethon import errors as telethon_errors

from tests.conftest import FakeClient, make_session_fake, ns
from tests.test_cli_media import _media_message
from tgcli.cli import main
from tgcli.errors import ConfigError, NotFoundError
from tgcli import session
from tgcli.commands import media as media_cmd


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
        return ns(id=5), ns(file=ns(name=f"{source.message_id}.bin"))

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
        return ns(id=5), ns(file=ns(name=f"{source.message_id}.bin"))

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
    return ns(id=5), ns(file=ns(name=f"{source.message_id}.bin"))


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


def test_media_download_rejects_message_id_with_bulk_flags(config_env, monkeypatch):
    make_session_fake(monkeypatch, FakeClient())
    assert main(["media", "download", "@chan", "42", "--message-ids", "1,2"]) == 2


def test_media_download_message_ids_combine_with_filters_and_limit(
    config_env, monkeypatch, tmp_path, capsys
):
    messages = {
        10: _media_message(10, "photo", date=datetime(2026, 7, 22, tzinfo=UTC)),
        11: _media_message(11, "video", date=datetime(2026, 7, 18, tzinfo=UTC)),
        12: _media_message(12, "video", date=datetime(2026, 7, 22, tzinfo=UTC)),
        13: _media_message(13, "video", date=datetime(2026, 7, 23, tzinfo=UTC)),
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


def test_media_download_type_filter_skips_text_only_explicit_id(
    config_env, monkeypatch, tmp_path, capsys
):
    text = _media_message(10, "photo", date=datetime(2026, 7, 22, tzinfo=UTC))
    text.media = None
    text.file = None
    video = _media_message(11, "video", date=datetime(2026, 7, 22, tzinfo=UTC))
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


def test_media_download_session_revoked_keeps_auth_exit_code(
    config_env, monkeypatch, tmp_path, capsys
):
    client = FakeClient(entities={"@chan": ns(id=5, title="C")})

    @asynccontextmanager
    async def fake_session(account, *, mutation_safe=False):
        try:
            yield client
        except telethon_errors.SessionRevokedError as exc:
            raise ConfigError("session needs reauthentication") from exc

    async def fake_resolve(tg, source, account_alias):
        return ns(id=5), _media_message(source.message_id, "video")

    async def revoked_download(tg, source, account_alias, **kwargs):
        raise telethon_errors.SessionRevokedError(request=None)

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
