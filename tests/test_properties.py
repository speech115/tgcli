"""Property-based guards for the defect classes the 1.2.16 audit surfaced.

Line coverage never sees these classes (bf-01 silent HTML truncation, bf-02
astral-offset desync, af-08 crash-on-malformed-state): each property pins an
invariant over generated input instead of an example. ``derandomize=True``
keeps the gate deterministic — the suite must never be flaky (ADR-0059).
"""

from __future__ import annotations

import string

from hypothesis import given, settings, strategies as st
from telethon.tl.types import MessageEntityBold

from tgcli import chatref
from tgcli.clone.state import CloneState
from tgcli.errors import PolicyError
from tgcli.formatting import render

DETERMINISTIC = settings(max_examples=200, derandomize=True)

# Includes astral plane characters (emoji) on purpose: Telegram entity
# offsets count UTF-16 code units, so astral input is where offset bugs live.
TEXT = st.text(
    alphabet=st.characters(
        codec="utf-8", categories=("L", "N", "P", "S", "Zs"), include_characters="😀🐍"
    ),
    min_size=1,
    max_size=80,
)


def utf16_units(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


@DETERMINISTIC
@given(TEXT)
def test_plain_render_is_identity(text):
    assert render(text, "plain") == (text, None)
    assert render(text, None) == (text, None)


@DETERMINISTIC
@given(TEXT.filter(lambda t: t == t.strip()))
def test_html_escape_round_trips(text):
    """bf-01/bf-02 class: whatever the operator previewed is what renders —
    escaped input must come back byte-identical, astral chars included."""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    rendered, entities = render(escaped, "html")

    assert rendered == text
    assert not entities


@DETERMINISTIC
@given(
    st.text(
        alphabet=string.ascii_letters + string.digits + "😀я", min_size=1, max_size=40
    )
)
def test_html_without_markup_is_identity(text):
    rendered, entities = render(text, "html")

    assert rendered == text
    assert not entities


@DETERMINISTIC
@given(st.text(max_size=80))
def test_html_render_is_total(text):
    """Arbitrary input either renders or fails closed with the documented
    PolicyError — never a traceback class, never silence."""
    try:
        rendered, _ = render(text, "html")
    except PolicyError:
        return
    assert isinstance(rendered, str)


BOLD_SAFE = st.text(
    alphabet=string.ascii_letters + string.digits + "😀яё", min_size=1, max_size=30
)


@DETERMINISTIC
@given(BOLD_SAFE, BOLD_SAFE)
def test_md_bold_offsets_count_utf16_units(prefix, inner):
    """bf-02 class: entity offsets after astral characters must count UTF-16
    code units, not Python code points."""
    rendered, entities = render(f"{prefix} **{inner}** tail", "md")

    assert rendered == f"{prefix} {inner} tail"
    bolds = [e for e in entities or [] if isinstance(e, MessageEntityBold)]
    assert len(bolds) == 1
    assert bolds[0].offset == utf16_units(prefix) + 1
    assert bolds[0].length == utf16_units(inner)


@DETERMINISTIC
@given(st.text(max_size=20))
def test_chatref_parse_is_total_and_typed(chat):
    """A bare (optionally negative) digit string becomes an int; everything
    else passes through unchanged; nothing raises."""
    result = chatref.parse(chat)

    body = chat.lstrip("-")
    if body.isdigit():
        assert isinstance(result, int)
        assert str(result).lstrip("-") == str(int(body))
    else:
        assert result == chat


JSONISH = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**53), max_value=2**53)
    | st.text(max_size=12),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=12,
)


@DETERMINISTIC
@given(st.dictionaries(st.text(max_size=16), JSONISH, max_size=8))
def test_clone_state_from_dict_fails_closed(data):
    """af-08 class: `load()` — from_dict's only caller — wraps exactly
    (KeyError, TypeError, ValueError) into the documented PolicyError.
    Anything outside that set (AttributeError, IndexError, ...) would
    escape as a traceback, so from_dict must never raise it."""
    try:
        state = CloneState.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return
    assert isinstance(state, CloneState)
