"""SLK-02 · The button value, version 1, and REQ-09 (button values are untrusted)."""

import json

import pytest

from slack_translator.domain.action_values import ActionValue, ActionValueError, encode, parse
from slack_translator.domain.languages import LanguageCode

FULL = ActionValue(
    channel="C0123ABCDE",
    ts="1795478400.000100",
    thread_root_ts="1795478000.000050",
    language=LanguageCode("zh"),
    script="Hant",
)


def test_a_value_survives_a_round_trip() -> None:
    assert parse(encode(FULL)) == FULL


def test_optional_fields_are_left_out_of_the_encoding() -> None:
    value = ActionValue(channel="C0123ABCDE", ts="1795478400.000100")
    assert json.loads(encode(value)) == {"v": 1, "c": "C0123ABCDE", "ts": "1795478400.000100"}
    assert parse(encode(value)) == value


def test_the_encoding_matches_the_contract_example() -> None:
    assert json.loads(encode(FULL)) == {
        "v": 1,
        "c": "C0123ABCDE",
        "ts": "1795478400.000100",
        "tr": "1795478000.000050",
        "l": "zh",
        "s": "Hant",
    }


def test_the_encoding_is_far_below_slacks_limit() -> None:
    assert len(encode(FULL)) < 200


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "[]",
        '"text"',
        "{}",
        '{"v":2,"c":"C0123ABCDE","ts":"1795478400.000100"}',
        '{"c":"C0123ABCDE","ts":"1795478400.000100"}',
        '{"v":1,"ts":"1795478400.000100"}',
        '{"v":1,"c":"C0123ABCDE"}',
        '{"v":1,"c":"c0123abcde","ts":"1795478400.000100"}',
        '{"v":1,"c":"C0123ABCDE","ts":"1795478400"}',
        '{"v":1,"c":"C0123ABCDE","ts":"1795478400.000100","tr":"nope"}',
        '{"v":1,"c":"C0123ABCDE","ts":"1795478400.000100","l":"korean"}',
        '{"v":1,"c":"C0123ABCDE","ts":"1795478400.000100","l":"ko","s":"Hans"}',
        '{"v":1,"c":"C0123ABCDE","ts":"1795478400.000100","s":"Hans"}',
        '{"v":1,"c":"C0123ABCDE","ts":"1795478400.000100","l":"zh","s":"Latn"}',
        '{"v":1,"c":"C0123ABCDE","ts":"1795478400.000100","text":"smuggled"}',
        '{"v":true,"c":"C0123ABCDE","ts":"1795478400.000100"}',
    ],
)
def test_anything_else_is_rejected(raw: str) -> None:
    with pytest.raises(ActionValueError):
        parse(raw)
