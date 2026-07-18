import pytest
from telethon.tl import types

from tgcli.confirm import confirmed_ids
from tgcli.errors import PolicyError


def test_confirmed_ids_matches_random_ids():
    response = type("R", (), {"updates": [types.UpdateMessageID(id=42, random_id=7)]})()
    assert confirmed_ids(response, [7]) == [42]


def test_confirmed_ids_accepts_short_sent_message():
    response = types.UpdateShortSentMessage(
        id=42, pts=1, pts_count=1, date=None, out=True
    )
    assert confirmed_ids(response, [7]) == [42]


def test_confirmed_ids_fails_closed_on_missing_confirmation():
    response = type("R", (), {"updates": []})()
    with pytest.raises(PolicyError):
        confirmed_ids(response, [7])
