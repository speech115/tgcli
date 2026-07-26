"""Tests for optional rich-text formatting (tgcli.formatting)."""

import pytest
from telethon.tl.types import (
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityCustomEmoji,
    MessageEntityItalic,
    MessageEntityPre,
    MessageEntitySpoiler,
    MessageEntityStrike,
    MessageEntityTextUrl,
    MessageEntityUnderline,
)

from tgcli import formatting
from tgcli.errors import PolicyError


def _types(entities):
    return [type(e) for e in entities]


def _utf16_slice(text, entity):
    units = text.encode("utf-16-le")
    start = entity.offset * 2
    return units[start : start + entity.length * 2].decode("utf-16-le")


def test_plain_returns_text_and_no_entities():
    assert formatting.render("**bold** literal", "plain") == ("**bold** literal", None)
    assert formatting.render("x", None) == ("x", None)


def test_html_bold_italic():
    text, entities = formatting.render("<b>жир</b> и <i>кур</i>", "html")
    assert text == "жир и кур"
    assert _types(entities) == [MessageEntityBold, MessageEntityItalic]
    assert (entities[0].offset, entities[0].length) == (0, 3)
    assert (entities[1].offset, entities[1].length) == (6, 3)


def test_html_spoiler():
    text, entities = formatting.render("до <tg-spoiler>секрет</tg-spoiler>", "html")
    assert text == "до секрет"
    assert _types(entities) == [MessageEntitySpoiler]
    assert (entities[0].offset, entities[0].length) == (3, 6)


def test_html_span_spoiler():
    text, entities = formatting.render('<span class="tg-spoiler">x</span>', "html")
    assert text == "x"
    assert _types(entities) == [MessageEntitySpoiler]


def test_html_expandable_blockquote():
    text, entities = formatting.render(
        "<blockquote expandable>цитата</blockquote>", "html"
    )
    assert text == "цитата"
    assert _types(entities) == [MessageEntityBlockquote]
    assert entities[0].collapsed is True


def test_html_plain_blockquote_not_collapsed():
    _text, entities = formatting.render("<blockquote>a</blockquote>", "html")
    assert entities[0].collapsed in (False, None)


def test_html_custom_emoji():
    text, entities = formatting.render(
        '<tg-emoji emoji-id="5301234567890123456">🔥</tg-emoji> жги', "html"
    )
    assert text == "🔥 жги"
    assert _types(entities) == [MessageEntityCustomEmoji]
    assert entities[0].document_id == 5301234567890123456


def test_html_offsets_are_utf16_after_emoji():
    # 💸 is a surrogate pair: length 2 in UTF-16, so the bold entity after it
    # must start at offset 3 (emoji=2 + space=1), not 2.
    text, entities = formatting.render("💸 <b>жир</b>", "html")
    assert text == "💸 жир"
    assert _types(entities) == [MessageEntityBold]
    assert entities[0].offset == 3
    assert entities[0].length == 3


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
    # The stdlib parser leaves an unfinished tag in rawdata and drops it, which
    # used to truncate the message *after* the operator approved the preview.
    with pytest.raises(PolicyError) as excinfo:
        formatting.render(text, "html")
    assert excinfo.value.exit_code == 2


@pytest.mark.parametrize(
    "text",
    ["List<int> is generic", "a <marquee>b</marquee> c", "x </closed> y"],
)
def test_html_unsupported_tag_is_blocked(text):
    # An unsupported tag parses as real markup and silently disappears from the
    # sent body; it must be an error instead of a deletion.
    with pytest.raises(PolicyError) as excinfo:
        formatting.render(text, "html")
    assert excinfo.value.exit_code == 2


def test_html_comment_is_blocked():
    with pytest.raises(PolicyError):
        formatting.render("a <!-- note --> b", "html")


def test_html_bare_angle_brackets_still_render():
    assert formatting.render("5 < 6 and 7 > 8", "html") == ("5 < 6 and 7 > 8", None)


def test_html_supported_tags_render_unchanged():
    text, entities = formatting.render(
        "<b>b</b><i>i</i><u>u</u><s>s</s>"
        "<blockquote>q</blockquote><blockquote expandable>e</blockquote>"
        '<tg-spoiler>sp</tg-spoiler><span class="tg-spoiler">sn</span>'
        "<code>c</code><pre>p</pre>"
        '<a href="https://example.com">l</a>'
        '<tg-emoji emoji-id="5301234567890123456">🔥</tg-emoji>',
        "html",
    )
    assert text == "biusqespsncpl🔥"
    assert _types(entities) == [
        MessageEntityBold,
        MessageEntityItalic,
        MessageEntityUnderline,
        MessageEntityStrike,
        MessageEntityBlockquote,
        MessageEntityBlockquote,
        MessageEntitySpoiler,
        MessageEntitySpoiler,
        MessageEntityCode,
        MessageEntityPre,
        MessageEntityTextUrl,
        MessageEntityCustomEmoji,
    ]
    assert [(e.offset, e.length) for e in entities] == [
        (0, 1),
        (1, 1),
        (2, 1),
        (3, 1),
        (4, 1),
        (5, 1),
        (6, 2),
        (8, 2),
        (10, 1),
        (11, 1),
        (12, 1),
        (13, 2),
    ]
    assert entities[10].url == "https://example.com"
    assert entities[11].document_id == 5301234567890123456


def test_html_astral_charref_keeps_entity_offsets_in_utf16():
    # convert_charrefs expands &#128512; after the input was surrogate encoded,
    # so the bold run has to be re-measured in UTF-16 code units.
    text, entities = formatting.render("&#128512; <b>жир</b>", "html")
    assert text == "😀 жир"
    assert _types(entities) == [MessageEntityBold]
    assert _utf16_slice(text, entities[0]) == "жир"


def test_html_astral_charref_inside_entity_is_measured_in_utf16():
    text, entities = formatting.render("<b>&#128512;ok</b> tail", "html")
    assert text == "😀ok tail"
    assert _utf16_slice(text, entities[0]) == "😀ok"


def test_plain_and_md_ignore_html_markup():
    assert formatting.render("if a<b then c", "plain") == ("if a<b then c", None)
    assert formatting.render("if a<b then c", None) == ("if a<b then c", None)
    assert formatting.render("if a<b then c", "md") == ("if a<b then c", None)
