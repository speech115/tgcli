import json

import pytest

from tests.conftest import FakeClient, make_session_fake, ns
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


def test_count_json_uses_messages_total(config_env, monkeypatch, capsys):
    entity = ns(id=-1001234, title="Channel")
    fake = FakeClient(entities={"@chan": entity}, message_total=73)
    make_session_fake(monkeypatch, fake)

    code = main(["count", "@chan", "--json"])

    assert code == 0
    assert fake.get_messages_calls == [(entity, None, 0)]
    assert json.loads(capsys.readouterr().out) == {
        "dialog": {"id": -1001234, "name": "Channel"},
        "count": 73,
    }


def test_count_plain_is_exact_single_tsv_value(config_env, monkeypatch, capsys):
    entity = ns(id=-1001234, title="Channel")
    make_session_fake(
        monkeypatch, FakeClient(entities={"@chan": entity}, message_total=73)
    )

    code = main(["count", "@chan", "--plain"])

    assert code == 0
    assert capsys.readouterr().out == "73\n"
