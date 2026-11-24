"""REQ-09 and SPK-03/04 · Reading Slack payloads. Anything unexpected is rejected, not guessed."""

from typing import Any

import pytest

from slack_translator.delivery.payloads import parse_offer_click, parse_retry_click, parse_shortcut
from slack_translator.domain.action_values import ActionValue, encode
from slack_translator.domain.languages import LanguageCode


def shortcut_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "message_action",
        "callback_id": "translate_message",
        "trigger_id": "13345224609.738474920.8088930838d88f008e0",
        "response_url": "https://hooks.slack.test/app/T0HOME/1/abc",
        "user": {"id": "U0MEMBER", "name": "ana", "team_id": "T0HOME"},
        "channel": {"id": "C0123ABCDE", "name": "support"},
        "message": {
            "type": "message",
            "user": "U0CUSTOMER",
            "ts": "1795478400.000100",
            "text": "hola",
        },
    }
    body.update(overrides)
    return body


class TestShortcut:
    def test_a_top_level_message(self) -> None:
        request = parse_shortcut(shortcut_body(), correlation_id="cid")
        assert request is not None
        assert request.entry == "shortcut"
        assert request.channel == "C0123ABCDE"
        assert request.user_id == "U0MEMBER"
        assert request.team_id == "T0HOME"
        assert request.text == "hola"
        assert request.ref.ts == "1795478400.000100"
        assert request.ref.thread_root_ts is None
        assert request.response_url is not None
        assert request.response_url.reveal() == "https://hooks.slack.test/app/T0HOME/1/abc"

    def test_a_thread_reply_keeps_its_thread_root(self) -> None:
        message = {
            "user": "U1",
            "ts": "1795478400.000200",
            "thread_ts": "1795478000.000050",
            "text": "x",
        }
        request = parse_shortcut(shortcut_body(message=message), correlation_id="cid")
        assert request is not None
        assert request.ref.thread_root_ts == "1795478000.000050"

    def test_a_thread_root_counts_as_top_level(self) -> None:
        message = {
            "user": "U1",
            "ts": "1795478400.000100",
            "thread_ts": "1795478400.000100",
            "text": "x",
        }
        request = parse_shortcut(shortcut_body(message=message), correlation_id="cid")
        assert request is not None
        assert request.ref.thread_root_ts is None

    def test_a_missing_team_id_is_left_unset_for_a_users_info_lookup(self) -> None:
        body = shortcut_body(user={"id": "U0MEMBER", "name": "ana"})
        request = parse_shortcut(body, correlation_id="cid")
        assert request is not None
        assert request.team_id is None

    def test_a_message_without_text_has_an_empty_text(self) -> None:
        message = {"user": "U1", "ts": "1795478400.000100"}
        request = parse_shortcut(shortcut_body(message=message), correlation_id="cid")
        assert request is not None
        assert request.text == ""

    @pytest.mark.parametrize(
        "broken",
        [
            {"user": {"name": "ana"}},
            {"channel": {"name": "support"}},
            {"message": {"text": "no ts"}},
            {"message": {"ts": "not-a-timestamp", "text": "x"}},
            {"response_url": 5},
            {"user": "U0MEMBER"},
        ],
    )
    def test_a_payload_missing_what_it_needs_is_rejected(self, broken: dict[str, Any]) -> None:
        assert parse_shortcut(shortcut_body(**broken), correlation_id="cid") is None


def click_body(value: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "block_actions",
        "user": {"id": "U0MEMBER", "team_id": "T0HOME"},
        "channel": {"id": "C0123ABCDE"},
        "container": {"type": "message", "channel_id": "C0123ABCDE", "is_ephemeral": True},
        "response_url": "https://hooks.slack.test/actions/T0HOME/1/abc",
        "actions": [{"action_id": "translate_retry", "type": "button", "value": value}],
    }
    body.update(overrides)
    return body


VALUE = encode(ActionValue(channel="C0123ABCDE", ts="1795478400.000100"))


class TestRetryClick:
    def test_a_valid_click_names_the_message_to_read_again(self) -> None:
        request = parse_retry_click(click_body(VALUE), correlation_id="cid")
        assert request is not None
        assert request.entry == "retry"
        assert request.text is None
        assert request.ref.channel == "C0123ABCDE"
        assert request.ref.ts == "1795478400.000100"
        assert request.user_id == "U0MEMBER"

    def test_the_thread_root_comes_from_the_value(self) -> None:
        value = encode(
            ActionValue(
                channel="C0123ABCDE", ts="1795478400.000200", thread_root_ts="1795478000.000050"
            )
        )
        request = parse_retry_click(click_body(value), correlation_id="cid")
        assert request is not None
        assert request.ref.thread_root_ts == "1795478000.000050"

    def test_the_channel_may_come_from_the_container_when_the_payload_has_none(self) -> None:
        body = click_body(VALUE)
        del body["channel"]
        assert parse_retry_click(body, correlation_id="cid") is not None

    def test_a_value_for_another_channel_than_the_click_is_rejected(self) -> None:
        other = encode(ActionValue(channel="C0OTHER123", ts="1795478400.000100"))
        assert parse_retry_click(click_body(other), correlation_id="cid") is None

    @pytest.mark.parametrize(
        "value", ["", "not json", '{"v":1}', '{"v":2,"c":"C0123ABCDE","ts":"1795478400.000100"}']
    )
    def test_a_tampered_value_is_rejected(self, value: str) -> None:
        assert parse_retry_click(click_body(value), correlation_id="cid") is None

    def test_a_click_without_actions_is_rejected(self) -> None:
        assert parse_retry_click(click_body(VALUE, actions=[]), correlation_id="cid") is None


class TestOfferClick:
    def test_a_valid_click_carries_the_offered_language_and_names_the_message(self) -> None:
        value = encode(
            ActionValue(
                channel="C0123ABCDE",
                ts="1795478400.000100",
                thread_root_ts="1795478000.000050",
                language=LanguageCode("zh"),
                script="Hant",
            )
        )
        request = parse_offer_click(click_body(value), correlation_id="cid")
        assert request is not None
        assert request.entry == "offer"
        assert request.text is None
        assert (request.language, request.script) == ("zh", "Hant")
        assert request.ref.thread_root_ts == "1795478000.000050"
        assert request.user_id == "U0MEMBER"

    def test_an_offer_value_without_a_language_is_rejected(self) -> None:
        assert parse_offer_click(click_body(VALUE), correlation_id="cid") is None

    def test_a_value_for_another_channel_than_the_click_is_rejected(self) -> None:
        other = encode(
            ActionValue(channel="C0OTHER123", ts="1795478400.000100", language=LanguageCode("ko"))
        )
        assert parse_offer_click(click_body(other), correlation_id="cid") is None

    @pytest.mark.parametrize("value", ["", "not json", '{"v":1}'])
    def test_a_tampered_value_is_rejected(self, value: str) -> None:
        assert parse_offer_click(click_body(value), correlation_id="cid") is None
