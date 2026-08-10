"""Unit tests for the pure clone fidelity classifiers."""

from types import SimpleNamespace

from telethon.tl import types

from tgcli.clone import fidelity


def _message(**overrides):
    values = {"id": 2, "media": None}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_a_message_without_a_keyboard_reports_no_loss():
    assert fidelity.dropped_buttons(_message()) is None
    assert fidelity.dropped_buttons(_message(reply_markup=None)) is None


def test_keyboard_rows_are_reported_in_order_with_their_class_and_label():
    markup = types.ReplyInlineMarkup(
        rows=[
            types.KeyboardButtonRow(
                buttons=[
                    types.KeyboardButtonUrl(text="Site", url="https://example.com"),
                    types.KeyboardButtonCallback(text="Vote", data=b"v"),
                ]
            ),
            types.KeyboardButtonRow(
                buttons=[types.KeyboardButtonSwitchInline(text="Share", query="q")]
            ),
        ]
    )

    assert fidelity.dropped_buttons(_message(reply_markup=markup)) == [
        {"type": "KeyboardButtonUrl", "text": "Site"},
        {"type": "KeyboardButtonCallback", "text": "Vote"},
        {"type": "KeyboardButtonSwitchInline", "text": "Share"},
    ]


def test_a_markup_that_carries_no_button_is_not_a_loss():
    """`ReplyKeyboardHide` and an empty row set are keyboard *instructions*, not
    chrome the destination is missing; reporting them would inflate the count."""
    hidden = _message(reply_markup=types.ReplyKeyboardHide())
    assert fidelity.dropped_buttons(hidden) is None
    assert (
        fidelity.dropped_buttons(
            _message(reply_markup=types.ReplyInlineMarkup(rows=[]))
        )
        is None
    )
    assert (
        fidelity.dropped_buttons(
            _message(
                reply_markup=types.ReplyInlineMarkup(
                    rows=[types.KeyboardButtonRow(buttons=[])]
                )
            )
        )
        is None
    )
