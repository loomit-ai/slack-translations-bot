"""UI-03, UI-06, UI-07, UI-09 and the no-text sentence: one section, identical fallback."""

import pytest

from slack_translator.delivery.views.notices import (
    already_read_message,
    no_text_message,
    not_allowed_message,
    progress_message,
    unavailable_message,
)


@pytest.mark.parametrize(
    ("message", "sentence"),
    [
        (progress_message(), "Translating…"),
        (already_read_message("English"), "This message is already in English."),
        (unavailable_message(), "This message is no longer available."),
        (not_allowed_message(), "Translation is available only in approved channels."),
        (no_text_message(), "This message has no text to translate."),
    ],
)
def test_each_notice_is_a_single_section_with_an_identical_fallback(message, sentence: str) -> None:
    assert message.text == sentence
    assert len(message.blocks) == 1
    block = message.blocks[0]
    assert block["type"] == "section"
    assert block["text"]["text"] == sentence
