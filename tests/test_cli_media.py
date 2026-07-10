import json

import pytest

from tests.conftest import FakeClient, make_session_fake
from tgcli import cli
from tgcli.cli import main


SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""

RESULT = {
    "source": "@channel:42",
    "path": "/tmp/clip.bin",
    "bytes": 6,
    "resumed": False,
    "parallel": 1,
}


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def test_media_download_json_reports_progress_only_on_stderr(
    config_env, monkeypatch, capsys
):
    make_session_fake(monkeypatch, FakeClient())

    async def fake_download(tg, source, account_alias, **kwargs):
        assert source.chat == "@channel"
        assert source.message_id == 42
        assert account_alias == "main"
        kwargs["progress"](3, 6)
        return RESULT

    monkeypatch.setattr(cli.media_cmd, "download_media", fake_download)

    assert main(["--json", "media", "download", "@channel", "42"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == RESULT
    assert captured.err == "downloaded 3/6 bytes\n"


def test_media_download_accepts_complete_tme_link(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, FakeClient())

    async def fake_download(tg, source, account_alias, **kwargs):
        assert source.chat == "@channel"
        assert source.message_id == 42
        return RESULT

    monkeypatch.setattr(cli.media_cmd, "download_media", fake_download)

    assert main(["--json", "media", "download", "t.me/channel/42"]) == 0
    assert json.loads(capsys.readouterr().out) == RESULT
