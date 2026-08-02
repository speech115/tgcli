"""Request-type registry and seam pin (ADR-0072, plan phase 0).

Covers #139's matrix rows S3 and S4, plus the registry's own contract: the
cooldown key is derived from the request type so it covers types nobody
listed, while the paced class is a lookup that may legitimately miss.
"""

import pytest
from telethon.client.users import UserMethods
from telethon.tl.functions import channels, contacts, messages, updates, upload

from tgcli.errors import ConfigError
from tgcli.governor import registry, seam


def _history(*, limit: int):
    return messages.GetHistoryRequest(
        peer="p",
        offset_id=0,
        offset_date=None,
        add_offset=0,
        limit=limit,
        max_id=0,
        min_id=0,
        hash=0,
    )


def _dialogs():
    return messages.GetDialogsRequest(
        offset_date=None, offset_id=0, offset_peer="p", limit=100, hash=0
    )


def test_request_key_is_namespaced_by_telethon_module():
    """Two namespaces share type names; Telegram treats them as distinct."""
    assert (
        registry.request_key(messages.GetMessagesRequest(id=[1]))
        == "messages.GetMessagesRequest"
    )
    assert (
        registry.request_key(
            channels.GetMessagesRequest(channel="c", id=[1]),
        )
        == "channels.GetMessagesRequest"
    )


def test_an_unlisted_request_type_still_gets_its_own_cooldown_key():
    """The gate must cover requests nobody enumerated, including `tg api`."""

    class SomeFutureRequest:
        pass

    key = registry.request_key(SomeFutureRequest())

    assert key.endswith("SomeFutureRequest")
    assert registry.classify(SomeFutureRequest()) is registry.UNLISTED
    assert registry.interval_for(SomeFutureRequest()) is None


@pytest.mark.parametrize(
    ("request_object", "expected"),
    [
        (_history(limit=100), "history"),
        (_dialogs(), "enumeration"),
        (messages.GetMessagesRequest(id=[1]), "by_id"),
        (upload.GetFileRequest(location=None, offset=0, limit=1), "media"),
        (contacts.ResolvePhoneRequest("+100"), "resolve_phone"),
        (updates.GetStateRequest(), "metadata"),
    ],
)
def test_known_request_types_land_in_their_adr_class(request_object, expected):
    assert registry.classify(request_object) == expected


def test_history_reads_carry_the_three_second_interval():
    """ADR-0072 decision 3's default, and the one the incident lacked."""
    history = _history(limit=1000)

    assert registry.interval_for(history) == 3.0


def test_mutations_are_gated_but_unpaced():
    """No interval, yet still keyed — a flood on a send arms its own record."""
    send = messages.SendMessageRequest("p", "hi")

    assert registry.interval_for(send) is None
    assert registry.request_key(send) == "messages.SendMessageRequest"


def test_get_messages_by_id_is_charged_per_batch_of_300():
    assert registry.BY_ID_BATCH == 300
    assert registry.interval_for(messages.GetMessagesRequest(id=[1])) == 10.0


def test_the_real_telethon_call_seam_matches_what_the_wrapper_expects():
    """Matrix S3: the private-API pin itself, against the real install."""
    seam.verify_seam(UserMethods)


def test_a_missing_call_seam_refuses_at_construction(monkeypatch):
    """Matrix S4: never silently run ungoverned."""

    class Clientless:
        pass

    with pytest.raises(ConfigError, match="cannot govern"):
        seam.verify_seam(Clientless)


def test_a_reshaped_call_seam_refuses_at_construction():
    class Reshaped:
        async def _call(self, request, sender):  # parameters swapped
            return None

    with pytest.raises(ConfigError, match="signature changed"):
        seam.verify_seam(Reshaped)


def test_a_synchronous_call_seam_refuses_at_construction():
    class Synchronous:
        def _call(self, sender, request):
            return None

    with pytest.raises(ConfigError, match="not a coroutine"):
        seam.verify_seam(Synchronous)
