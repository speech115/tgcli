"""Tests for optional rich-text formatting (tgcli.formatting)."""

from telethon.tl.types import (
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityCustomEmoji,
    MessageEntityItalic,
    MessageEntitySpoiler,
)

from tgcli import formatting


def _types(entities):
    return [type(e) for e in entities]


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
