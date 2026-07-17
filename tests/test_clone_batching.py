"""Unit tests for the pure clone batch planner."""

import asyncio
from types import SimpleNamespace

import pytest

from tgcli.clone import batching
from tgcli.errors import PolicyError


def _msg(message_id, *, action=None, grouped_id=None):
    return SimpleNamespace(id=message_id, action=action, grouped_id=grouped_id)


def _events(messages):
    async def stream():
        for message in messages:
            yield message

    async def collect():
        return [event async for event in batching.plan(stream())]

    return asyncio.run(collect())


def _shape(events):
    return [
        (
            type(event).__name__,
            event.message_id
            if isinstance(event, batching.ServiceSkip)
            else [message.id for message in event.messages],
        )
        for event in events
    ]


def test_singles_become_single_batches():
    events = _events([_msg(1), _msg(2)])
    assert _shape(events) == [("Batch", [1]), ("Batch", [2])]


def test_service_message_yields_skip():
    events = _events([_msg(1), _msg(2, action="join"), _msg(3)])
    assert _shape(events) == [("Batch", [1]), ("ServiceSkip", 2), ("Batch", [3])]


def test_album_accumulates_and_flushes_on_single():
    events = _events([_msg(1, grouped_id=7), _msg(2, grouped_id=7), _msg(3)])
    assert _shape(events) == [("Batch", [1, 2]), ("Batch", [3])]


def test_album_flushes_on_group_change():
    events = _events([_msg(1, grouped_id=7), _msg(2, grouped_id=8)])
    assert _shape(events) == [("Batch", [1]), ("Batch", [2])]


def test_album_flushes_on_service_message():
    events = _events([_msg(1, grouped_id=7), _msg(2, action="pin")])
    assert _shape(events) == [("Batch", [1]), ("ServiceSkip", 2)]


def test_trailing_album_flushes_at_stream_end():
    events = _events([_msg(1, grouped_id=7), _msg(2, grouped_id=7)])
    assert _shape(events) == [("Batch", [1, 2])]


def test_empty_stream_yields_nothing():
    assert _events([]) == []


@pytest.mark.parametrize("bad", [True, "7", 1.5])
def test_invalid_grouped_id_raises_policy_error(bad):
    with pytest.raises(PolicyError, match="album group id is invalid"):
        _events([_msg(1, grouped_id=bad)])


def test_service_skip_message_identity():
    """ServiceSkip.message must be the original service message object."""
    service_msg = _msg(42, action="join")
    events = _events([service_msg])
    assert len(events) == 1
    assert isinstance(events[0], batching.ServiceSkip)
    assert events[0].message is service_msg
