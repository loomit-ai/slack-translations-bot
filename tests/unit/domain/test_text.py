"""REQ-07 (cleaning for detection) and REQ-08 (model output is shown as data)."""

import pytest

from slack_translator.domain.text import (
    MIN_LETTERS,
    clean_for_detection,
    letter_count,
    neutralize_controls,
)


class TestCleanForDetection:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("hello <@U123ABC> there", "hello there"),
            ("<@U123ABC|anna> please check <#C0123ABCD|support>", "please check"),
            ("<!channel> <!here> <!subteam^S123|team> ok", "ok"),
            ("see <https://example.test/a?b=1|the docs> now", "see now"),
            ("see <https://example.test/a> now", "see now"),
            ("mail <mailto:a@example.test|a@example.test>", "mail"),
            ("bare https://example.test/path link", "bare link"),
            ("nice :smile: work :+1::skin-tone-2:", "nice work"),
            ("run `rm -rf x` first", "run first"),
            ("before\n```\nline one\nline two\n```\nafter", "before after"),
            ("a &lt;b&gt; &amp; c", "a <b> & c"),
            ("  spaced \n\n out\t text ", "spaced out text"),
        ],
    )
    def test_removes_what_is_not_language(self, raw: str, expected: str) -> None:
        assert clean_for_detection(raw) == expected

    def test_keeps_times_that_look_like_emoji_codes(self) -> None:
        assert "10:30:45" in clean_for_detection("at 10:30:45 today")

    def test_text_in_other_scripts_is_untouched(self) -> None:
        assert clean_for_detection("파란 상자는 어디에 있나요?") == "파란 상자는 어디에 있나요?"


class TestLetterCount:
    @pytest.mark.parametrize(
        ("text", "count"),
        [
            ("", 0),
            ("   ", 0),
            ("12 34 !?", 0),
            ("ab", 2),
            ("a b c", 3),
            ("상자", 2),
            ("파란 상자", 4),
            ("你好吗", 3),
            ("😀😀😀", 0),
        ],
    )
    def test_counts_unicode_letters_only(self, text: str, count: int) -> None:
        assert letter_count(text) == count

    def test_the_minimum_is_three_letters_as_the_flow_specification_proposes(self) -> None:
        assert MIN_LETTERS == 3


class TestNeutralizeControls:
    def test_a_link_the_model_added_is_shown_as_plain_characters(self) -> None:
        shown = neutralize_controls("Visit <https://evil.test|here> now", source="Besuchen Sie uns")
        assert "<https" not in shown
        assert "&lt;https://evil.test|here&gt;" in shown

    @pytest.mark.parametrize(
        "token", ["<!channel>", "<!here>", "<@U123ABC>", "<#C0123ABCD|general>"]
    )
    def test_mentions_the_model_added_are_shown_as_plain_characters(self, token: str) -> None:
        shown = neutralize_controls(f"Hello {token}", source="Hallo")
        assert token not in shown
        assert token.replace("<", "&lt;").replace(">", "&gt;") in shown

    def test_tokens_that_were_in_the_source_are_left_untouched(self) -> None:
        source = "Bitte <@U123ABC> sehen: <https://example.test|docs>"
        translated = "Please look <@U123ABC>: <https://example.test|docs>"
        assert neutralize_controls(translated, source) == translated

    def test_only_the_token_missing_from_the_source_is_escaped(self) -> None:
        source = "Hallo <@U123ABC>"
        shown = neutralize_controls("Hello <@U123ABC> and <!channel>", source)
        assert shown == "Hello <@U123ABC> and &lt;!channel&gt;"

    def test_ordinary_text_and_line_breaks_are_unchanged(self) -> None:
        text = "Line one\nLine two with a < b and c > d\n`code`"
        assert neutralize_controls(text, source="x") == text
