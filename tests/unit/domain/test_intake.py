"""FLOW-02 step 1 and the meaning of a result: which events are classified, and what then."""

from dataclasses import replace
from typing import Any

import pytest

from slack_translator.domain.detection import Detection
from slack_translator.domain.intake import (
    Admitted,
    Dropped,
    MessageEvent,
    classify_result,
    customer_language,
    known_language,
    screen,
)
from slack_translator.domain.languages import LanguageCode, parse_language_seed
from slack_translator.domain.ports import DropReason

CHANNEL = "C0123ABCDE"
LANGUAGES = parse_language_seed("en:team,es:team,ru:customer,ko:customer,vi:customer,zh:customer")


def served(channel: str) -> bool:
    return channel == CHANNEL


def event(**changes: Any) -> MessageEvent:
    base = MessageEvent(
        event_id="Ev0123ABCDE",
        channel=CHANNEL,
        ts="1700000000.000100",
        thread_root_ts=None,
        user_id="U0123ABCDE",
        text="Hola, ¿puedes ayudarme con esto?",
        subtype=None,
        bot_id=None,
        channel_type="channel",
    )
    return replace(base, **changes)


def code(value: str) -> LanguageCode:
    return LanguageCode(value)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"channel": "C9999999999"}, "not_allowlisted"),
        ({"subtype": "message_changed"}, "subtype"),
        ({"subtype": "message_deleted"}, "subtype"),
        ({"subtype": "channel_join"}, "subtype"),
        ({"subtype": "bot_message"}, "subtype"),
        ({"bot_id": "B0123ABCDE"}, "bot"),
        ({"text": None}, "empty"),
        ({"text": ""}, "empty"),
        ({"text": " \n\t "}, "empty"),
        ({"text": "<@U0123ABCDE> <https://example.com|link> :tada: `x = 1`"}, "too_short"),
        ({"text": "```\nprint(1)\n``` <#C0123ABCDE|general>"}, "too_short"),
        ({"text": "ok"}, "too_short"),
        ({"text": "한"}, "too_short"),
        ({"text": "123 456 !!!"}, "too_short"),
    ],
)
def test_an_excluded_event_names_the_reason_it_is_dropped(
    changes: dict[str, Any], reason: DropReason
) -> None:
    assert screen(event(**changes), served) == Dropped(reason)


@pytest.mark.parametrize("channel_type", ["im", "mpim", "app_home", "something_new"])
def test_a_conversation_that_is_not_a_channel_is_dropped_without_asking_if_it_is_served(
    channel_type: str,
) -> None:
    asked: list[str] = []

    def served_spy(channel: str) -> bool:
        asked.append(channel)
        return True

    assert screen(event(channel_type=channel_type), served_spy) == Dropped("not_allowlisted")
    assert asked == []


@pytest.mark.parametrize("channel_type", ["channel", "group"])
def test_public_and_private_channels_are_classified(channel_type: str) -> None:
    assert isinstance(screen(event(channel_type=channel_type), lambda channel: True), Admitted)


def test_without_a_conversation_type_the_channel_id_decides() -> None:
    assert isinstance(
        screen(event(channel_type=None, channel="C0123ABCDE"), lambda c: True), Admitted
    )
    assert isinstance(
        screen(event(channel_type=None, channel="G0123ABCDE"), lambda c: True), Admitted
    )
    assert screen(event(channel_type=None, channel="D0123ABCDE"), lambda c: True) == Dropped(
        "not_allowlisted"
    )


@pytest.mark.parametrize("subtype", ["file_share", "thread_broadcast", None])
def test_file_shares_and_thread_broadcasts_are_classified_like_plain_messages(
    subtype: str | None,
) -> None:
    assert isinstance(screen(event(subtype=subtype), served), Admitted)


@pytest.mark.parametrize("text", ["abc", "한국어", "Три", "x y z"])
def test_three_letters_in_any_script_are_enough(text: str) -> None:
    assert isinstance(screen(event(text=text), served), Admitted)


def test_the_first_matching_reason_is_the_one_reported() -> None:
    assert screen(event(channel="C9999999999", bot_id="B1", text=""), served) == Dropped(
        "not_allowlisted"
    )
    assert screen(event(subtype="bot_message", bot_id="B1"), served) == Dropped("subtype")
    assert screen(event(bot_id="B1", text=""), served) == Dropped("bot")
    assert screen(event(text="  "), served) == Dropped("empty")


def test_an_admitted_event_carries_the_cleaned_text_not_the_original() -> None:
    result = screen(event(text="Hola <@U0123ABCDE> :tada: gracias <https://x.com|aquí>"), served)

    assert result == Admitted(cleaned_text="Hola gracias")


def test_the_served_check_is_the_only_place_that_knows_which_channels_count() -> None:
    assert screen(event(channel="C7777777777"), lambda channel: True) == Admitted(
        cleaned_text="Hola, ¿puedes ayudarme con esto?"
    )
    assert screen(event(), lambda channel: False) == Dropped("not_allowlisted")


@pytest.mark.parametrize(
    ("detection", "expected"),
    [
        (Detection(code("ko"), 0.9), "customer"),
        (Detection(code("ko"), 0.7), "customer"),
        (Detection(code("ko"), 0.699999), "low_confidence"),
        (Detection(code("zh"), 1.0), "customer"),
        (Detection(code("en"), 0.95), "team"),
        (Detection(code("es"), 0.7), "team"),
        (Detection(code("en"), 0.5), "low_confidence"),
        (Detection("other", 0.98), "other"),
        (Detection("other", 0.0), "other"),
        (Detection(code("fr"), 0.9), "other"),
        (None, "error"),
    ],
)
def test_a_result_is_customer_team_other_low_confidence_or_error(
    detection: Detection | None, expected: str
) -> None:
    assert classify_result(detection, LANGUAGES, 0.7) == expected


def test_only_a_confident_customer_language_gives_an_offer() -> None:
    korean = customer_language(Detection(code("ko"), 0.8), LANGUAGES, 0.7)
    assert korean is not None
    assert (korean.code, korean.name) == ("ko", "Korean")
    assert customer_language(Detection(code("ko"), 0.6), LANGUAGES, 0.7) is None
    assert customer_language(Detection(code("en"), 0.99), LANGUAGES, 0.7) is None
    assert customer_language(Detection("other", 0.99), LANGUAGES, 0.7) is None
    assert customer_language(None, LANGUAGES, 0.7) is None


def test_a_language_that_is_not_configured_is_counted_as_other() -> None:
    assert known_language(Detection(code("ko"), 0.9), LANGUAGES) == "ko"
    assert known_language(Detection(code("fr"), 0.9), LANGUAGES) == "other"
    assert known_language(Detection("other", 0.9), LANGUAGES) == "other"
    assert known_language(None, LANGUAGES) == "other"
