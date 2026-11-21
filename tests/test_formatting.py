"""Tests for optional rich-text formatting (tgcli.formatting)."""

import pytest
from telethon.tl.types import (
    MessageEntityBold,
    MessageEntitySpoiler,
)

from tgcli import formatting
from tgcli.errors import PolicyError


def _types(entities):
    return [type(e) for e in entities]


def test_html_span_spoiler():
    text, entities = formatting.render('<span class="tg-spoiler">x</span>', "html")
    assert text == "x"
    assert _types(entities) == [MessageEntitySpoiler]


def test_html_plain_blockquote_not_collapsed():
    _text, entities = formatting.render("<blockquote>a</blockquote>", "html")
    assert entities[0].collapsed in (False, None)


def test_md_bold():
    text, entities = formatting.render("**жир** тут", "md")
    assert text == "жир тут"
    assert _types(entities) == [MessageEntityBold]


def test_unknown_format_raises():
    try:
        formatting.render("x", "rtf")
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown format")


@pytest.mark.parametrize("text", ["if a<b then c", "x<y", "tail <b>bold"])
def test_html_unterminated_markup_is_blocked(text):
    with pytest.raises(PolicyError) as excinfo:
        formatting.render(text, "html")
    assert excinfo.value.exit_code == 2


@pytest.mark.parametrize(
    "text",
    ["List<int> is generic", "a <marquee>b</marquee> c", "x </closed> y"],
)
def test_html_unsupported_tag_is_blocked(text):
    with pytest.raises(PolicyError) as excinfo:
        formatting.render(text, "html")
    assert excinfo.value.exit_code == 2


def test_html_comment_is_blocked():
    with pytest.raises(PolicyError):
        formatting.render("a <!-- note --> b", "html")
