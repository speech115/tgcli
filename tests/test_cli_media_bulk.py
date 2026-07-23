import json

import pytest

from tests.conftest import FakeClient, make_session_fake, ns
from tgcli import cli
from tgcli.cli import main
from tgcli.errors import NotFoundError, PolicyError


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

    monkeypatch.setattr(cli.media_cmd, "download_media", fake_download)
    monkeypatch.setattr(cli.media_cmd, "resolve_message", fake_resolve)

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

    monkeypatch.setattr(cli.media_cmd, "download_media", fake_download)
    monkeypatch.setattr(cli.media_cmd, "resolve_message", fake_resolve)

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


def test_media_download_rejects_over_100_ids(config_env, monkeypatch):
    make_session_fake(monkeypatch, FakeClient(entities={"@chan": ns(id=5, title="C")}))
    ids = ",".join(str(i) for i in range(1, 102))
    assert main(["media", "download", "@chan", "--message-ids", ids]) == 2


def test_media_download_rejects_message_id_with_bulk_flags(config_env, monkeypatch):
    make_session_fake(monkeypatch, FakeClient())
    assert (
        main(["media", "download", "@chan", "42", "--message-ids", "1,2"]) == 2
    )
