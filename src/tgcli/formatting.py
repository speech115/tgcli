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

import re

from telethon.extensions import markdown
from telethon.extensions.html import HTMLToTelegramParser
from telethon.helpers import add_surrogate, del_surrogate, strip_text
from telethon.tl.types import MessageEntitySpoiler

from tgcli.errors import PolicyError

FORMATS = ("plain", "md", "html")

# Everything ADR-0030 and docs/CONTRACT.md document for --format html. Anything
# else parses as markup and is silently deleted from the body, so it is an
# error: the operator approved a preview of the full text.
SUPPORTED_HTML_TAGS = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "code",
        "del",
        "em",
        "i",
        "pre",
        "s",
        "span",
        "spoiler",
        "strong",
        "tg-emoji",
        "tg-spoiler",
        "u",
    }
)


class _HtmlParser(HTMLToTelegramParser):
    """Telethon's HTML parser plus ``<tg-spoiler>`` / ``<span class=tg-spoiler>``."""

    def handle_starttag(self, tag, attrs):
        _reject_unsupported(tag)
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

    def handle_endtag(self, tag):
        _reject_unsupported(tag)
        super().handle_endtag(tag)

    def handle_data(self, text):
        # HTMLParser runs with convert_charrefs=True, so a charref is expanded
        # after the input was surrogate encoded and arrives here as a real code
        # point. Re-encode (idempotent for already-surrogated data) so entity
        # offsets keep counting UTF-16 code units.
        super().handle_data(add_surrogate(text))

    def handle_comment(self, data):
        _reject_markup(f"comment {del_surrogate(data)!r}")

    def handle_decl(self, decl):
        _reject_markup(f"declaration {del_surrogate(decl)!r}")

    def unknown_decl(self, data):
        _reject_markup(f"declaration {del_surrogate(data)!r}")

    def handle_pi(self, data):
        _reject_markup(f"processing instruction {del_surrogate(data)!r}")


def _reject_markup(what: str):
    raise PolicyError(f"unsupported html markup: {what}", format="html")


def _reject_unsupported(tag: str):
    if tag not in SUPPORTED_HTML_TAGS:
        _reject_markup(f"tag <{del_surrogate(tag)}>")


def _parse_html(text: str):
    if not text:
        return text, []
    parser = _HtmlParser()
    parser.feed(add_surrogate(text))
    # feed() leaves an unfinished tag in rawdata and drops it from .text; never
    # call close(), which flushes rawdata and hides the truncation.
    if parser.rawdata:
        raise PolicyError(
            f"unterminated html markup: {del_surrogate(parser.rawdata)!r}",
            format="html",
        )
    if parser._building_entities:
        unclosed = ", ".join(f"<{tag}>" for tag in parser._building_entities)
        raise PolicyError(f"unclosed html markup: {unclosed}", format="html")
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


def mask_phone(phone: str | None) -> str:
    """Redact a phone for JSON, plain, stderr, and audit (ADR-0042)."""
    if not phone:
        return ""
    if len(phone) <= 4:
        return "…" + phone[-2:] if len(phone) >= 2 else "…"
    return f"{phone[:2]}…{phone[-2:]}"


# Free-form exception text can echo phones with display punctuation even when
# the CLI normalized its input. Require a leading `+` and at least four digits,
# but allow common separators between them; bare numeric ids stay untouched.
_EMBEDDED_PHONE = re.compile(r"\+\d(?:[ ()\t.-]*\d){3,}")


def mask_phones_in_text(text: str) -> str:
    """Redact `+`-prefixed phone-shaped substrings inside free-form text."""
    return _EMBEDDED_PHONE.sub(lambda match: mask_phone(match.group()), text)
