"""Optional rich-text formatting for outgoing messages.

Telegram does not store markup inline; it carries formatting as a list of
``MessageEntity`` offsets measured in UTF-16 code units. We reuse Telethon's
HTML parser, which already emits bold, italic, underline, strike, blockquote
(including the ``expandable`` variant), code, pre, links and ``tg-emoji`` custom
emoji, and extend it with spoiler support, which Telethon 1.44 does not emit.

Formats:

* ``plain`` — text is sent verbatim, no entities.
* ``md``    — Telethon Markdown (bold/italic/code/strike/link).
* ``html``  — the full тень.exe set: bold, italic, underline, strike, quote,
              expandable quote, spoiler, code/pre, links, custom emoji.
"""

from telethon.extensions import markdown
from telethon.extensions.html import HTMLToTelegramParser
from telethon.helpers import add_surrogate, del_surrogate, strip_text
from telethon.tl.types import MessageEntitySpoiler

FORMATS = ("plain", "md", "html")


class _HtmlParser(HTMLToTelegramParser):
    """Telethon's HTML parser plus ``<tg-spoiler>`` / ``<span class=tg-spoiler>``."""

    def handle_starttag(self, tag, attrs):
        is_spoiler = tag in ("tg-spoiler", "spoiler") or (
            tag == "span" and dict(attrs).get("class") == "tg-spoiler"
        )
        if not is_spoiler:
            super().handle_starttag(tag, attrs)
            return
        self._open_tags.appendleft(tag)
        self._open_tags_meta.appendleft(None)
        if tag not in self._building_entities:
            self._building_entities[tag] = MessageEntitySpoiler(
                offset=len(self.text), length=0
            )


def _parse_html(text: str):
    if not text:
        return text, []
    parser = _HtmlParser()
    parser.feed(add_surrogate(text))
    stripped = strip_text(parser.text, parser.entities)
    parser.entities.reverse()
    parser.entities.sort(key=lambda entity: entity.offset)
    return del_surrogate(stripped), parser.entities


def render(text: str, fmt: str | None):
    """Return ``(clean_text, entities_or_None)`` for the requested format."""
    if fmt in (None, "plain"):
        return text, None
    if fmt == "md":
        parsed, entities = markdown.parse(text)
        return parsed, entities or None
    if fmt == "html":
        parsed, entities = _parse_html(text)
        return parsed, entities or None
    raise ValueError(f"unknown format: {fmt!r}")
