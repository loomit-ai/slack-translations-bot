"""UI-04, UI-05 and the long-translation rule: shown whole, in sections, never cut silently."""

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from slack_translator.delivery.views.ui_04_result import (
    MAX_SHOWN_CHARS,
    SECTION_LIMIT,
    result_message,
    split_sections,
)


def section_texts(message_blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    return [block["text"]["text"] for block in message_blocks if block["type"] == "section"]


def test_a_known_source_gets_a_heading_and_the_translation_on_its_own_section() -> None:
    message = result_message("English translation from Korean", "Where is the blue box?")
    assert section_texts(message.blocks) == [
        "*English translation from Korean*",
        "Where is the blue box?",
    ]
    assert message.text == "English translation from Korean\nWhere is the blue box?"


def test_the_translation_is_shown_as_mrkdwn_so_protected_tokens_stay_active() -> None:
    message = result_message("English translation", "hi <@U123ABC>")
    assert message.blocks[1]["text"]["type"] == "mrkdwn"


def test_no_action_is_offered_in_milestone_0_1() -> None:
    message = result_message("English translation", "text")
    assert all(block["type"] != "actions" for block in message.blocks)


def test_line_breaks_are_kept_in_the_fallback_text() -> None:
    message = result_message("English translation", "one\ntwo\n\nthree")
    assert message.text.endswith("one\ntwo\n\nthree")


@pytest.mark.parametrize("text", ["", "a", "x" * 2999, "x" * 3000])
def test_text_that_fits_stays_in_one_chunk(text: str) -> None:
    assert split_sections(text) == ([text] if text else [])


def test_long_text_is_split_without_losing_a_character() -> None:
    text = "".join(f"line {i} " + "word " * 40 + "\n" for i in range(120))
    chunks = split_sections(text)
    assert len(chunks) > 1
    assert "".join(chunks) == text
    assert all(len(chunk) <= SECTION_LIMIT for chunk in chunks)


def test_text_without_any_break_is_cut_at_the_limit() -> None:
    text = "x" * (SECTION_LIMIT * 2 + 10)
    chunks = split_sections(text)
    assert "".join(chunks) == text
    assert [len(chunk) for chunk in chunks] == [SECTION_LIMIT, SECTION_LIMIT, 10]


def test_chunks_prefer_to_end_at_a_line_break() -> None:
    first = "a" * (SECTION_LIMIT - 10) + "\n"
    chunks = split_sections(first + "b" * 500)
    assert chunks[0] == first


def test_a_long_translation_is_shown_whole_in_consecutive_sections() -> None:
    text = "".join(f"line {i} " + "word " * 40 + "\n" for i in range(100))
    assert len(text) < MAX_SHOWN_CHARS
    message = result_message("English translation", text)
    sections = section_texts(message.blocks)
    assert sections[0] == "*English translation*"
    assert "".join(sections[1:]) == text.strip() or "".join(sections[1:]).strip() == text.strip()
    assert all(len(part) <= SECTION_LIMIT for part in sections)
    assert len(message.blocks) <= 50


def test_a_result_the_size_of_the_largest_slack_message_is_shown_whole() -> None:
    text = "".join(f"Line {i}: the blue box is on the table.\n" for i in range(1000))
    assert len(text) > 36_000
    message = result_message("English translation", text)
    sections = section_texts(message.blocks)
    assert "".join(sections[1:]) == text
    assert len(message.blocks) <= 50
    assert "Showing the first" not in "".join(sections)
    assert len(message.text) <= 40_000
    assert message.text.endswith("The full translation is shown in the message.")


def test_a_fallback_that_fits_has_no_pointer_and_keeps_every_character() -> None:
    text = "word " * 6000
    message = result_message("English translation", text)
    assert message.text == f"English translation\n{text}"


def test_a_translation_beyond_every_limit_says_so_instead_of_cutting_silently() -> None:
    text = "word " * (MAX_SHOWN_CHARS // 5 + 2000)
    message = result_message("English translation", text)
    last = section_texts(message.blocks)[-1]
    assert "Showing the first" in last
    assert f"of {len(text)} characters" in last
    assert len(message.blocks) <= 50
    assert len(message.text) <= 40_000
