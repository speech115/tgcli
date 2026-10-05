"""Cursor codec tests for the archive sync update cursor (ADR-0063)."""

from __future__ import annotations

import base64
import json

import pytest
from hypothesis import given, strategies as st

from tgcli.changes_cursor import (
    ChangesCursor,
    decode,
    encode,
    with_channel,
    without_channel,
)
from tgcli.errors import PolicyError


def test_encode_decode_round_trip():
    cursor = ChangesCursor(
        pts=10, qts=2, date=1_700_000_000, seq=4, channels={-1001234: 50}
    )
    assert decode(encode(cursor)) == cursor


def test_encode_is_opaque_v1_prefix():
    text = encode(ChangesCursor(pts=1, qts=0, date=0, seq=0))
    assert text.startswith("v1:")
    assert "+" not in text and "/" not in text


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "v0:abc",
        "v1:",
        "v1:!!!",
        "v1:" + base64.urlsafe_b64encode(b"not-json").decode().rstrip("="),
        "v1:"
        + base64.urlsafe_b64encode(
            json.dumps({"pts": -1, "qts": 0, "date": 0, "seq": 0}).encode()
        )
        .decode()
        .rstrip("="),
        "v1:"
        + base64.urlsafe_b64encode(
            json.dumps(
                {"pts": 1, "qts": 0, "date": 0, "seq": 0, "channels": {"123": 1}}
            ).encode()
        )
        .decode()
        .rstrip("="),
    ],
)
def test_decode_rejects_adversarial_input(bad):
    with pytest.raises(PolicyError, match="changes cursor"):
        decode(bad)


def test_with_and_without_channel():
    base = ChangesCursor(pts=1, qts=0, date=0, seq=0)
    added = with_channel(base, -1009, 3)
    assert added.channels[-1009] == 3
    dropped = without_channel(added, -1009)
    assert dropped.channels == {}
    with pytest.raises(PolicyError, match="not a subscribed"):
        without_channel(base, -1009)


@given(
    pts=st.integers(min_value=0, max_value=10**9),
    qts=st.integers(min_value=0, max_value=10**9),
    date=st.integers(min_value=0, max_value=2_000_000_000),
    seq=st.integers(min_value=0, max_value=10**9),
    channels=st.dictionaries(
        keys=st.integers(min_value=-(10**12), max_value=-1),
        values=st.integers(min_value=0, max_value=10**9),
        max_size=5,
    ),
)
def test_property_round_trip(pts, qts, date, seq, channels):
    cursor = ChangesCursor(pts=pts, qts=qts, date=date, seq=seq, channels=channels)
    assert decode(encode(cursor)) == cursor


@given(noise=st.binary(min_size=1, max_size=64))
def test_property_tampered_payload_is_policy_error(noise):
    good = encode(ChangesCursor(pts=1, qts=0, date=0, seq=0))
    tampered = "v1:" + base64.urlsafe_b64encode(noise).decode().rstrip("=")
    if tampered == good:
        return
    with pytest.raises(PolicyError):
        decode(tampered)


def test_a_broken_cursor_points_at_archive_rebaseline():
    """`tg changes` is gone; the remedy must name a command that exists."""
    with pytest.raises(PolicyError, match="run: tg archive rebaseline"):
        decode("v1:not-base64!")
