"""ADR-005 · Simplified or Traditional, decided from the characters of the text."""

import pytest

from slack_translator.domain.chinese_script import script_of

Z1 = "请把三颗纸星星放进蓝色盒子里。"
Z2 = "請把三顆紙星星放進藍色盒子裡。"


def test_simplified_text_is_hans_and_traditional_text_is_hant() -> None:
    assert script_of(Z1) == "Hans"
    assert script_of(Z2) == "Hant"


@pytest.mark.parametrize(
    "text",
    [
        "我你他是",
        "后干里",
        "",
        "   ",
        "hello world",
        "파란 상자에 종이 별이 세 개 있어요.",
        "Привет, мир",
        "。，、！？",
        "123 456",
    ],
)
def test_text_that_does_not_decide_has_no_script(text: str) -> None:
    assert script_of(text) is None


def test_characters_that_exist_in_both_scripts_are_not_counted() -> None:
    assert script_of("后干里后干里国") == "Hans"
    assert script_of("后干里后干里國") == "Hant"


def test_the_script_with_more_deciding_characters_wins() -> None:
    assert script_of("请进國") == "Hans"
    assert script_of("請進国") == "Hant"


def test_a_tie_does_not_decide() -> None:
    assert script_of("国國") is None


def test_other_scripts_around_chinese_do_not_matter() -> None:
    assert script_of("Hello 请把 world 进来 :tada: 123") == "Hans"
    assert script_of("Hello 請把 world 進來") == "Hant"
