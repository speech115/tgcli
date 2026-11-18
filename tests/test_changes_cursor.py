"""Cursor codec tests for the archive sync common update cursor."""

from __future__ import annotations

import base64
import json

import pytest
from hypothesis import given, strategies as st

from tgcli.archive.changes_cursor import ChangesCursor, decode, encode
from tgcli.errors import PolicyError


def test_encode_decode_round_trip():
    cursor = ChangesCursor(pts=10, qts=2, date=1_700_000_000, seq=4)
    assert decode(encode(cursor)) == cursor


def test_a_cursor_from_channel_polling_days_decodes_without_its_channels():
    raw = json.dumps(
        {"pts": 1, "qts": 0, "date": 0, "seq": 0, "channels": {"-1009": 3}}
    ).encode()
    old = "v1:" + base64.urlsafe_b64encode(raw).decode().rstrip("=")
    assert decode(old) == ChangesCursor(pts=1, qts=0, date=0, seq=0)


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
    ],
)
def test_decode_rejects_adversarial_input(bad):
    with pytest.raises(PolicyError, match="changes cursor"):
        decode(bad)


@given(
    pts=st.integers(min_value=0, max_value=10**9),
    qts=st.integers(min_value=0, max_value=10**9),
    date=st.integers(min_value=0, max_value=2_000_000_000),
    seq=st.integers(min_value=0, max_value=10**9),
)
def test_property_round_trip(pts, qts, date, seq):
    cursor = ChangesCursor(pts=pts, qts=qts, date=date, seq=seq)
    assert decode(encode(cursor)) == cursor


@given(noise=st.binary(min_size=1, max_size=64))
def test_property_tampered_payload_is_policy_error(noise):
    good = encode(ChangesCursor(pts=1, qts=0, date=0, seq=0))
    tampered = "v1:" + base64.urlsafe_b64encode(noise).decode().rstrip("=")
    if tampered == good:
        return
    with pytest.raises(PolicyError):
        decode(tampered)
