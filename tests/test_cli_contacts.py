import json

import pytest
from telethon.tl import functions

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


def _user(id, first_name, last_name, username, contact=True, bot=False):
    return ns(
        id=id,
        first_name=first_name,
        last_name=last_name,
        username=username,
        bot=bot,
        contact=contact,
    )


def test_contacts_list_returns_all_contacts(config_env, monkeypatch, capsys):
    ivan = _user(1, "Ivan", "Petrov", "ivanp")
    bob = _user(2, "Bob", "Smith", "bobs")
    client = FakeClient(contacts_result=ns(users=[ivan, bob]))
    make_session_fake(monkeypatch, client)

    assert main(["contacts", "list", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data == {
        "contacts": [
            {
                "id": 1,
                "type": "user",
                "username": "ivanp",
                "display_name": "Ivan Petrov",
                "is_contact": True,
                "is_bot": False,
            },
            {
                "id": 2,
                "type": "user",
                "username": "bobs",
                "display_name": "Bob Smith",
                "is_contact": True,
                "is_bot": False,
            },
        ]
    }

    assert any(
        isinstance(request, functions.contacts.GetContactsRequest)
        for request in client.call_requests
    )


def test_contacts_search_local_filters_and_avoids_global_search(
    config_env, monkeypatch, capsys
):
    ivan = _user(1, "Ivan", "Petrov", "ivanp")
    bob = _user(2, "Bob", "Smith", "bobs")
    client = FakeClient(contacts_result=ns(users=[ivan, bob]))
    make_session_fake(monkeypatch, client)

    assert main(["contacts", "search", "iva", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["scope"] == "local"
    assert [contact["id"] for contact in data["contacts"]] == [1]

    assert not any(
        isinstance(request, functions.contacts.SearchRequest)
        for request in client.call_requests
    )


def test_contacts_search_global_uses_search_request(config_env, monkeypatch, capsys):
    ivan = _user(3, "Ivan", "Global", "ivanglobal")
    client = FakeClient(
        contacts_result=ns(users=[]),
        contacts_search_result=ns(users=[ivan]),
    )
    make_session_fake(monkeypatch, client)

    assert main(["contacts", "search", "iva", "--global", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["scope"] == "global"
    assert [contact["id"] for contact in data["contacts"]] == [3]

    search_requests = [
        request
        for request in client.call_requests
        if isinstance(request, functions.contacts.SearchRequest)
    ]
    assert len(search_requests) == 1
    assert search_requests[0].q == "iva"


def test_contacts_search_local_case_insensitive_matches_username(
    config_env, monkeypatch, capsys
):
    user = _user(4, "Alice", None, "IvanFan")
    client = FakeClient(contacts_result=ns(users=[user]))
    make_session_fake(monkeypatch, client)

    assert main(["contacts", "search", "ivan", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert [contact["id"] for contact in data["contacts"]] == [4]


def test_contacts_plain_output_sanitizes_and_lists_all(config_env, monkeypatch, capsys):
    user = _user(5, "Mal\nformed", "Name", "mal\ttab")
    client = FakeClient(contacts_result=ns(users=[user]))
    make_session_fake(monkeypatch, client)

    assert main(["contacts", "list", "--plain"]) == 0

    out = capsys.readouterr().out.strip("\n")
    assert out == "5\tuser\tmal tab\tMal formed Name"
