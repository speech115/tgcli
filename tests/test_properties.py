"""Property-based guards for the defect classes the 1.2.16 audit surfaced.

Line coverage never sees these classes (bf-01 silent HTML truncation, bf-02
astral-offset desync, af-08 crash-on-malformed-state): each property pins an
invariant over generated input instead of an example. ``derandomize=True``
keeps the gate deterministic — the suite must never be flaky (ADR-0059).
"""

from __future__ import annotations

import contextlib
import sqlite3
import string

from hypothesis import HealthCheck, assume, given, settings, strategies as st
from telethon.tl.types import MessageEntityBold

from tgcli import chatref
from tgcli.clone import state, statedb
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


ROW_VALUE = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-3, 2**31 + 2),
    st.text(max_size=6),
    st.sampled_from(
        ["enabled", "none", "forum", "broadcast", "2026-07-15T12:00:00+00:00"]
    ),
)


@given(
    meta=st.dictionaries(st.sampled_from(statedb.META_FIELDS), ROW_VALUE, max_size=6),
    rows=st.lists(
        st.tuples(
            st.sampled_from(list(statedb.MapTable)),
            st.integers(-2, 2**31 + 1),
            st.integers(-2, 2**31 + 1),
        ),
        max_size=6,
    ),
)
@settings(
    max_examples=200,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_clone_state_load_fails_closed(meta, rows):
    """af-08 class: whatever a damaged database holds, `load` returns a state
    or raises the documented PolicyError. Anything else (AttributeError,
    OverflowError, ...) would escape as a traceback."""
    base = CloneState.new(account_user_id=1, source_peer_id=2, source_title="S")
    path = state.path_for(base.clone_id)
    path.unlink(missing_ok=True)
    state.save(base)
    conn = sqlite3.connect(path)
    try:
        for column, value in meta.items():
            with contextlib.suppress(sqlite3.IntegrityError):
                conn.execute(f"UPDATE meta SET {column} = ?", (value,))
        for table, source, dest in rows:
            with contextlib.suppress(sqlite3.IntegrityError):
                conn.execute(f"INSERT INTO {table} VALUES (?, ?)", (source, dest))
        conn.commit()
    finally:
        conn.close()

    try:
        loaded = state.load(base.clone_id)
    except PolicyError:
        return
    assert isinstance(loaded, CloneState)


@st.composite
def long_caption(draw):
    unit = draw(TEXT)
    text = unit * draw(st.integers(1, 3000 // len(unit) + 1))
    size = utf16_units(text)
    entities = [
        MessageEntityBold(offset=offset, length=draw(st.integers(1, size - offset)))
        for offset in draw(st.lists(st.integers(0, size - 1), max_size=5))
    ]
    return text, entities


@DETERMINISTIC
@given(long_caption())
def test_a_split_caption_fits_loses_no_text_and_keeps_every_entity(caption):
    from tgcli.clone.send import CAPTION_LIMIT, split_caption

    text, entities = caption
    assume(utf16_units(text) > CAPTION_LIMIT)
    originals = [(entity.offset, entity.length) for entity in entities]
    head, head_entities, rest, rest_entities = split_caption(text, entities)

    assert 0 < utf16_units(head) <= CAPTION_LIMIT
    assert text.startswith(head) and text.endswith(rest)
    assert text[len(head) : len(text) - len(rest)].strip() == ""
    # The source entities are not changed in place.
    assert [(e.offset, e.length) for e in entities] == originals
    head_end = utf16_units(head)
    rest_start = utf16_units(text) - utf16_units(rest)

    def covered(spans, shift=0):
        return {
            shift + at
            for offset, length in spans
            for at in range(offset, offset + length)
        }

    source = covered(originals)
    assert all(e.length > 0 for e in head_entities + rest_entities)
    assert covered((e.offset, e.length) for e in head_entities) == {
        at for at in source if at < head_end
    }
    assert covered(((e.offset, e.length) for e in rest_entities), rest_start) == {
        at for at in source if at >= rest_start
    }
