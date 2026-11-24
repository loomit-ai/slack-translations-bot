"""FLOW-01 through the real app: shortcut, Retry and every private outcome (TC-003, 010, 024, 025,
039, 041, 042, 051, 052, 053, 054, 060; FF-04). Made-up text only."""

import asyncio
import time
from typing import Any

import pytest
from tests.integration.conftest import Harness
from tests.support.fake_slack import RESPONSE_URL
from tests.support.payloads import (
    CHANNEL,
    MEMBER,
    THREAD_ROOT,
    TS,
    offer_payload,
    retry_payload,
    shortcut_payload,
)

from slack_translator.delivery.app import MissingScope
from slack_translator.domain.action_values import parse
from slack_translator.domain.call_context import current_attempt
from slack_translator.domain.detection import Detection
from slack_translator.domain.errors import ModelError, ModelErrorCategory
from slack_translator.domain.languages import LanguageCode
from slack_translator.domain.messages import MessageRef, OfferRequest

KOREAN = "파란 상자는 어디에 있나요?"
TIMEOUT = ModelError(ModelErrorCategory.TIMEOUT, "translate")


def shortcut(h: Harness, **overrides: Any) -> dict[str, Any]:
    return shortcut_payload(response_url=h.slack.response_url(), **overrides)


def response_url_posts(h: Harness) -> list[dict[str, Any]]:
    return [call.payload for call in h.slack.calls_of(RESPONSE_URL)]


def button_value(post: dict[str, Any]) -> str:
    [actions] = [block for block in post["blocks"] if block["type"] == "actions"]
    return actions["elements"][0]["value"]


class TestReadOnDemand:
    async def test_a_korean_message_is_translated_privately_for_the_member(
        self, h: Harness
    ) -> None:
        await h.deliver_and_settle(shortcut(h, text=KOREAN))

        assert h.classifier.calls == [KOREAN]
        [call] = h.translator.calls
        assert (call.text, call.target_language, call.source_language_hint) == (KOREAN, "en", "ko")
        [post] = response_url_posts(h)
        assert post["response_type"] == "ephemeral"
        assert post["replace_original"] is False
        assert post["text"] == "English translation from Korean\nWhere is the blue box?"
        h.slack.assert_no_public_writes()
        assert [e.fields["outcome"] for e in h.telemetry.named("translate_completed")] == ["shown"]
        [classified] = h.telemetry.named("classify_completed")
        assert classified.fields["result"] == "customer"

    async def test_the_translator_gets_the_original_text_and_detection_the_cleaned_text(
        self, h: Harness
    ) -> None:
        text = "see <https://example.test|docs> :smile: " + KOREAN
        await h.deliver_and_settle(shortcut(h, text=text))
        assert h.classifier.calls == ["see " + KOREAN]
        assert h.translator.calls[0].text == text

    async def test_a_thread_reply_is_answered_in_its_thread(self, h: Harness) -> None:
        await h.deliver_and_settle(shortcut(h, ts="1795478400.000200", thread_ts=THREAD_ROOT))
        assert response_url_posts(h)[0]["thread_ts"] == THREAD_ROOT

    async def test_a_long_translation_is_shown_whole_in_consecutive_sections(
        self, h: Harness
    ) -> None:
        long_text = "".join(f"Line {i}: the blue box is here.\n" for i in range(400))
        h.translator.script = [long_text]
        await h.deliver_and_settle(shortcut(h))
        [post] = response_url_posts(h)
        sections = [b["text"]["text"] for b in post["blocks"] if b["type"] == "section"]
        assert len(sections) > 2
        assert "".join(sections[1:]).replace("\n", "") == long_text.replace("\n", "")


class TestAcknowledgement:
    @pytest.mark.parametrize("listener", ["translate_message", "translate_retry"])
    async def test_every_listener_acknowledges_before_any_slow_work(
        self, h: Harness, listener: str
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.translator.hold = asyncio.Event()
        payload = (
            shortcut(h)
            if listener == "translate_message"
            else retry_payload(response_url=h.slack.response_url())
        )

        response, elapsed = await h.deliver(payload)

        assert response is not None and response.status == 200
        assert elapsed < 1
        before_ack = h.timeline[: h.timeline.index("telemetry:ack_observed")]
        assert before_ack == []
        h.translator.hold.set()
        await h.app.registry.drain()
        assert h.translator.calls

    async def test_every_registered_listener_has_an_acknowledgement_test(self, h: Harness) -> None:
        covered = {
            "translate_message",
            "translate_retry",
            # Bolt acknowledges events itself before it runs their listeners.
            "member_joined_channel",
            "member_left_channel",
            "message_event",
            "translate_offer",
        }
        assert set(h.app.listener_names) == covered


class TestNoUsableDetection:
    async def test_a_message_already_in_the_reading_language_is_not_translated(
        self, h: Harness
    ) -> None:
        h.classifier.result = Detection(LanguageCode("en"), 0.99)
        await h.deliver_and_settle(shortcut(h, text="I put three paper stars in the blue box."))
        assert h.translator.calls == []
        assert response_url_posts(h)[0]["text"] == "This message is already in English."

    async def test_the_reading_language_below_the_threshold_is_translated_without_a_name(
        self, h: Harness
    ) -> None:
        h.classifier.result = Detection(LanguageCode("en"), 0.5)
        await h.deliver_and_settle(shortcut(h))
        assert len(h.translator.calls) == 1
        assert response_url_posts(h)[0]["text"].startswith("English translation\n")

    @pytest.mark.parametrize(
        "result",
        [
            ModelError(ModelErrorCategory.TIMEOUT, "detect"),
            Detection("other", 0.9),
            Detection(LanguageCode("fr"), 0.99),
            Detection(LanguageCode("ko"), 0.4),
        ],
    )
    async def test_translation_goes_ahead_without_a_hint_or_a_language_name(
        self, h: Harness, result: Detection | ModelError
    ) -> None:
        h.classifier.result = result
        await h.deliver_and_settle(shortcut(h))
        [call] = h.translator.calls
        assert call.source_language_hint is None
        assert response_url_posts(h)[0]["text"] == "English translation\nWhere is the blue box?"

    async def test_a_classifier_that_never_answers_is_cut_off_at_its_deadline(
        self, h: Harness
    ) -> None:
        h.classifier.hold = asyncio.Event()
        await h.deliver_and_settle(shortcut(h))
        [call] = h.translator.calls
        assert call.source_language_hint is None
        [classified] = h.telemetry.named("classify_completed")
        assert classified.fields["result"] == "error"


class TestNothingToTranslate:
    @pytest.mark.parametrize(
        "text", ["", "   ", "<@U0ANA> :smile:", "ok", "😀😀😀", "https://example.test"]
    )
    async def test_a_message_without_enough_letters_gets_a_notice_and_no_model_call(
        self, h: Harness, text: str
    ) -> None:
        await h.deliver_and_settle(shortcut(h, text=text))
        assert h.classifier.calls == []
        assert h.translator.calls == []
        assert response_url_posts(h)[0]["text"] == "This message has no text to translate."
        h.slack.assert_no_public_writes()


class TestBoundary:
    async def test_a_user_from_another_workspace_gets_nothing_at_all(self, h: Harness) -> None:
        response = await h.deliver_and_settle(shortcut(h, user_team="T0OTHER"))
        assert response is not None and response.status == 200
        assert h.classifier.calls == [] and h.translator.calls == []
        assert h.slack.calls_of_kind("private_write") == []
        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == ["workspace"]

    async def test_an_unknown_workspace_is_looked_up_and_refused_when_it_is_not_home(
        self, h: Harness
    ) -> None:
        h.slack.add_user("U0EXT", team_id="T0OTHER")
        await h.deliver_and_settle(shortcut(h, user="U0EXT", user_team=None))
        assert len(h.slack.calls_of("users.info")) == 1
        assert h.slack.calls_of_kind("private_write") == []
        assert h.translator.calls == []

    async def test_a_user_who_cannot_be_looked_up_is_refused(self, h: Harness) -> None:
        await h.deliver_and_settle(shortcut(h, user="U0NOBODY", user_team=None))
        assert h.slack.calls_of_kind("private_write") == []
        assert h.translator.calls == []

    async def test_a_member_outside_the_allowlist_gets_a_private_notice_and_no_model_call(
        self, h: Harness
    ) -> None:
        await h.deliver_and_settle(shortcut(h, channel="C0OTHERXYZ"))
        assert h.classifier.calls == [] and h.translator.calls == []
        assert response_url_posts(h)[0]["text"] == "X3M Translator is unavailable in this channel."
        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == ["channel"]
        h.slack.assert_no_public_writes()

    async def test_a_direct_message_is_outside_the_allowlist(self, h: Harness) -> None:
        await h.deliver_and_settle(shortcut(h, channel="D0123ABCDE"))
        assert h.translator.calls == []


class TestFailureAndRetry:
    async def test_a_failed_translation_gives_a_private_error_with_retry(self, h: Harness) -> None:
        h.translator.script = [TIMEOUT]
        await h.deliver_and_settle(shortcut(h))
        [post] = response_url_posts(h)
        assert post["text"] == "We couldn’t translate this message. Retry translation."
        assert [e.fields["outcome"] for e in h.telemetry.named("translate_completed")] == ["failed"]
        h.slack.assert_no_public_writes()

    @pytest.mark.parametrize("answer", ["", "   \n"])
    async def test_an_empty_answer_counts_as_a_failed_translation(
        self, h: Harness, answer: str
    ) -> None:
        h.translator.script = [answer]
        await h.deliver_and_settle(shortcut(h))
        assert "Retry translation" in response_url_posts(h)[0]["text"]

    async def test_an_unexpected_error_still_ends_in_a_private_error_with_retry(
        self, h: Harness
    ) -> None:
        h.classifier.result = RuntimeError("secret text must not be logged")
        await h.deliver_and_settle(shortcut(h))
        assert "Retry translation" in response_url_posts(h)[0]["text"]
        [event] = h.telemetry.named("internal_error")
        assert event.fields["exception_type"] == "RuntimeError"
        assert "secret" not in repr(event)

    async def test_retry_reads_the_message_again_and_replaces_the_error_in_place(
        self, h: Harness
    ) -> None:
        h.translator.script = [TIMEOUT, TIMEOUT, "Where is the blue box?"]
        await h.deliver_and_settle(shortcut(h, text=KOREAN))
        value = button_value(response_url_posts(h)[0])
        h.slack.add_message(CHANNEL, TS, KOREAN)

        await h.deliver_and_settle(retry_payload(value=value, response_url=h.slack.response_url()))

        fetches = h.slack.calls_of("conversations.history")
        assert len(fetches) == 1 and fetches[0].payload["oldest"] == TS
        progress, result = response_url_posts(h)[1:]
        assert progress["text"] == "Translating…" and progress["replace_original"] is True
        assert result["text"] == "English translation from Korean\nWhere is the blue box?"
        assert result["replace_original"] is True
        h.slack.assert_no_public_writes()

    async def test_retry_on_a_thread_reply_reads_it_from_the_thread(self, h: Harness) -> None:
        reply_ts = "1795478400.000200"
        h.slack.add_message(CHANNEL, THREAD_ROOT, "root text")
        h.slack.add_message(CHANNEL, reply_ts, KOREAN, thread_ts=THREAD_ROOT)
        await h.deliver_and_settle(
            retry_payload(
                ts=reply_ts, thread_root_ts=THREAD_ROOT, response_url=h.slack.response_url()
            )
        )
        [fetch] = h.slack.calls_of("conversations.replies")
        assert fetch.payload["ts"] == THREAD_ROOT
        assert h.translator.calls[0].text == KOREAN

    async def test_retry_after_the_message_was_deleted_says_so_and_calls_no_model(
        self, h: Harness
    ) -> None:
        await h.deliver_and_settle(retry_payload(response_url=h.slack.response_url()))
        assert h.classifier.calls == [] and h.translator.calls == []
        [post] = response_url_posts(h)
        assert post["text"] == "This message is no longer available."
        assert post["replace_original"] is True

    async def test_a_tampered_button_value_is_dropped_without_output(self, h: Harness) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        response = await h.deliver_and_settle(
            retry_payload(value='{"v":1,"c":"C0OTHERXYZ","ts":"1795478400.000100"}')
        )
        assert response is not None and response.status == 200
        assert h.slack.calls_of("conversations.history") == []
        assert h.slack.calls_of_kind("private_write") == []
        assert h.translator.calls == []
        assert h.telemetry.named("internal_error")

    async def test_retry_from_another_workspace_gets_nothing(self, h: Harness) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        await h.deliver_and_settle(retry_payload(user_team="T0OTHER"))
        assert h.slack.calls_of("conversations.history") == []
        assert h.slack.calls_of_kind("private_write") == []


class TestDelivery:
    async def test_an_expired_response_url_falls_back_to_a_private_message_in_the_thread(
        self, h: Harness
    ) -> None:
        h.slack.fail_next(RESPONSE_URL, "used_url", status=404)
        await h.deliver_and_settle(shortcut(h, ts="1795478400.000200", thread_ts=THREAD_ROOT))
        [fallback] = h.slack.calls_of("chat.postEphemeral")
        assert fallback.payload["channel"] == CHANNEL
        assert fallback.payload["user"] == MEMBER
        assert fallback.payload["thread_ts"] == THREAD_ROOT
        assert fallback.payload["text"] == "English translation from Korean\nWhere is the blue box?"
        h.slack.assert_no_public_writes()

    async def test_a_response_url_is_given_up_after_five_uses(self, h: Harness) -> None:
        for _ in range(6):
            await h.deliver_and_settle(shortcut(h))
        assert len(h.slack.calls_of(RESPONSE_URL)) == 5
        assert len(h.slack.calls_of("chat.postEphemeral")) == 1

    async def test_when_both_ways_fail_it_is_counted_as_a_delivery_failure_not_as_shown(
        self, h: Harness
    ) -> None:
        h.slack.fail_next(RESPONSE_URL, "used_url", status=404)
        h.slack.fail_next("chat.postEphemeral", "channel_not_found")

        response = await h.deliver_and_settle(shortcut(h))

        assert response is not None and response.status == 200
        h.slack.assert_no_public_writes()
        outcomes = [e.fields["outcome"] for e in h.telemetry.named("translate_completed")]
        assert outcomes == ["delivery_failed"]
        assert len(h.slack.calls_of(RESPONSE_URL)) == 1
        assert len(h.slack.calls_of("chat.postEphemeral")) == 1
        assert h.telemetry.named("internal_error") == []


class TestModelOutputIsData:
    async def test_a_link_or_mention_the_model_added_is_shown_as_plain_characters(
        self, h: Harness
    ) -> None:
        h.translator.script = ["Visit <https://evil.test|here> now <!channel>"]
        await h.deliver_and_settle(shortcut(h, text="Besuchen Sie uns jetzt"))
        [post] = response_url_posts(h)
        shown = "".join(b["text"]["text"] for b in post["blocks"] if b["type"] == "section")
        assert "<https://evil.test" not in shown and "<!channel>" not in shown
        assert "&lt;!channel&gt;" in shown

    async def test_tokens_that_were_in_the_source_stay_active(self, h: Harness) -> None:
        h.translator.script = ["Please ask <@U0ANA>"]
        await h.deliver_and_settle(shortcut(h, text="Bitte frag <@U0ANA> jetzt"))
        [post] = response_url_posts(h)
        assert "<@U0ANA>" in post["text"]


class TestNothingLeaksIntoTelemetry:
    async def test_no_event_carries_message_text_on_any_path(self, h: Harness) -> None:
        marker_in = "SOURCEMARKER파란상자"
        marker_out = "TRANSLATIONMARKER"
        h.translator.script = [marker_out, TIMEOUT]
        await h.deliver_and_settle(shortcut(h, text=marker_in))
        await h.deliver_and_settle(shortcut(h, text=marker_in + " again"))
        everything = repr(h.telemetry.events)
        assert marker_in not in everything and marker_out not in everything
        assert h.slack.api_base_url  # the fake was in use
        assert h.telemetry.events


class TestStartup:
    async def test_a_channel_without_the_bot_is_warned_about_and_the_bot_still_starts(
        self, h: Harness
    ) -> None:
        h.slack.add_channel(CHANNEL, is_member=False)
        await h.app.prepare()
        assert [
            e.fields["channel_id"] for e in h.telemetry.named("startup_channel_not_member")
        ] == [CHANNEL]

    async def test_the_bot_is_not_ready_until_the_transport_says_so(self, h: Harness) -> None:
        assert not h.app.readiness.ready
        assert h.telemetry.named("startup_ready") == []

        h.app.mark_ready()

        assert h.app.readiness.ready
        assert len(h.telemetry.named("startup_ready")) == 1


class TestRegressionsFromQA:
    @pytest.mark.parametrize("listener", ["translate_message", "translate_retry"])
    async def test_a_failed_workspace_lookup_is_a_counted_refusal_with_no_output(
        self, h: Harness, listener: str
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        for _ in range(5):  # the Slack SDK retries a dropped connection a few times
            h.slack.drop_next("users.info")
        url = h.slack.response_url()
        payload = (
            shortcut_payload(user_team=None, response_url=url)
            if listener == "translate_message"
            else retry_payload(user_team=None, response_url=url)
        )

        response = await h.deliver_and_settle(payload)

        assert response is not None and response.status == 200
        assert h.classifier.calls == [] and h.translator.calls == []
        assert h.slack.calls_of_kind("private_write") == []
        assert h.slack.calls_of("conversations.history") == []
        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == ["unverified"]
        assert h.telemetry.named("internal_error") == []

    @pytest.mark.parametrize("status", [301, 302, 307])
    async def test_a_redirecting_response_url_falls_back_to_a_private_message_in_the_thread(
        self, h: Harness, status: int
    ) -> None:
        h.slack.fail_next(RESPONSE_URL, "moved", status=status)
        await h.deliver_and_settle(shortcut(h, ts="1795478400.000200", thread_ts=THREAD_ROOT))
        [fallback] = h.slack.calls_of("chat.postEphemeral")
        assert fallback.payload["user"] == MEMBER
        assert fallback.payload["thread_ts"] == THREAD_ROOT
        h.slack.assert_no_public_writes()

    async def test_a_result_of_about_37_thousand_characters_is_shown_whole(
        self, h: Harness
    ) -> None:
        text = "".join(f"Line {i}: the blue box is on the table.\n" for i in range(940))
        assert 36_000 < len(text) < 40_000
        h.translator.script = [text]
        await h.deliver_and_settle(shortcut(h))
        [post] = response_url_posts(h)
        sections = [b["text"]["text"] for b in post["blocks"] if b["type"] == "section"]
        assert "".join(sections[1:]) == text
        assert "Showing the first" not in "".join(sections)
        assert len(post["blocks"]) <= 50
        assert len(post["text"]) <= 40_000


class TestArchitectReview:
    async def test_a_request_is_counted_only_after_the_boundary_checks(self, h: Harness) -> None:
        await h.deliver_and_settle(shortcut(h, user_team="T0OTHER"))
        await h.deliver_and_settle(shortcut(h, channel="C0OTHERXYZ"))
        assert h.telemetry.named("translate_requested") == []

        await h.deliver_and_settle(shortcut(h))
        assert len(h.telemetry.named("translate_requested")) == 1

    @pytest.mark.parametrize("listener", ["translate_message", "translate_retry"])
    async def test_a_failure_before_the_actor_is_confirmed_shows_nothing(
        self, h: Harness, listener: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(*_: object) -> bool:
            raise RuntimeError("secret text must not be logged")

        monkeypatch.setattr("slack_translator.actions.translate_message.membership", explode)
        h.slack.add_message(CHANNEL, TS, KOREAN)
        url = h.slack.response_url()
        payload = (
            shortcut_payload(response_url=url)
            if listener == "translate_message"
            else retry_payload(response_url=url)
        )

        response = await h.deliver_and_settle(payload)

        assert response is not None and response.status == 200
        assert h.slack.calls_of_kind("private_write") == []
        assert h.classifier.calls == [] and h.translator.calls == []
        assert h.telemetry.named("translate_requested") == []
        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == ["unverified"]
        [event] = h.telemetry.named("internal_error")
        assert event.fields["exception_type"] == "RuntimeError"
        assert "secret" not in repr(event)


RATE_LIMITED = ModelError(ModelErrorCategory.RATE_LIMITED, "translate", 429)
BLUE_BOX = "Where is the blue box?"
RESULT = f"English translation from Korean\n{BLUE_BOX}"
FAILED = "We couldn’t translate this message. Retry translation."


class TestTheOneRetry:
    """CR-05: the action makes the one retry, and shows the error note beside it on Retry."""

    @pytest.mark.parametrize(
        "category",
        [
            ModelErrorCategory.TIMEOUT,
            ModelErrorCategory.RATE_LIMITED,
            ModelErrorCategory.UNAVAILABLE,
            ModelErrorCategory.INVALID_RESPONSE,
        ],
    )
    async def test_a_transient_failure_gets_one_more_try_after_a_short_pause(
        self, h: Harness, category: ModelErrorCategory
    ) -> None:
        attempts: list[int] = []

        async def note_attempt() -> None:
            attempts.append(current_attempt())

        h.translator.on_call = note_attempt
        h.translator.script = [ModelError(category, "translate"), BLUE_BOX]

        await h.deliver_and_settle(shortcut(h))

        assert len(h.translator.calls) == 2
        assert attempts == [1, 2]
        [pause] = h.clock.slept
        assert 0.3 <= pause <= 1.0
        assert [post["text"] for post in response_url_posts(h)] == [RESULT]

    async def test_an_empty_first_answer_is_retried_like_any_unusable_answer(
        self, h: Harness
    ) -> None:
        h.translator.script = ["  ", BLUE_BOX]

        await h.deliver_and_settle(shortcut(h))

        assert len(h.translator.calls) == 2
        assert [post["text"] for post in response_url_posts(h)] == [RESULT]

    async def test_a_short_retry_after_is_the_pause(self, h: Harness) -> None:
        h.translator.script = [
            ModelError(ModelErrorCategory.RATE_LIMITED, "translate", 429, 2.0),
            BLUE_BOX,
        ]

        await h.deliver_and_settle(shortcut(h))

        assert h.clock.slept == [2.0]
        assert len(h.translator.calls) == 2

    async def test_a_retry_after_over_five_seconds_means_no_second_attempt(
        self, h: Harness
    ) -> None:
        h.translator.script = [
            ModelError(ModelErrorCategory.UNAVAILABLE, "translate", 503, 5.01),
            BLUE_BOX,
        ]

        await h.deliver_and_settle(shortcut(h))

        assert len(h.translator.calls) == 1
        assert h.clock.slept == []
        assert [post["text"] for post in response_url_posts(h)] == [FAILED]

    async def test_a_failure_that_cannot_improve_is_not_retried(self, h: Harness) -> None:
        h.translator.script = [ModelError(ModelErrorCategory.REJECTED, "translate", 401), BLUE_BOX]

        await h.deliver_and_settle(shortcut(h))

        assert len(h.translator.calls) == 1
        assert [post["text"] for post in response_url_posts(h)] == [FAILED]

    async def test_two_failures_make_exactly_two_calls_and_end_in_the_error_with_retry(
        self, h: Harness
    ) -> None:
        h.translator.script = [TIMEOUT, RATE_LIMITED, BLUE_BOX]

        await h.deliver_and_settle(shortcut(h))

        assert len(h.translator.calls) == 2
        assert [post["text"] for post in response_url_posts(h)] == [FAILED]
        assert [e.fields["outcome"] for e in h.telemetry.named("translate_completed")] == ["failed"]

    async def test_an_unexpected_exception_is_not_retried(self, h: Harness) -> None:
        h.translator.script = [RuntimeError("secret text must not be logged"), BLUE_BOX]

        await h.deliver_and_settle(shortcut(h))

        assert len(h.translator.calls) == 1
        assert [post["text"] for post in response_url_posts(h)] == [FAILED]
        [event] = h.telemetry.named("internal_error")
        assert event.fields["exception_type"] == "RuntimeError"
        assert "secret" not in repr(event)

    @pytest.mark.parametrize("h", [{"TRANSLATOR_TIMEOUT": "1"}], indirect=True)
    async def test_a_translator_that_ignores_its_deadline_is_stopped_and_retried(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("slack_translator.actions.translate_message.ATTEMPT_MARGIN", -0.95)
        h.translator.script = [BLUE_BOX]
        h.translator.hold = asyncio.Event()

        await h.deliver_and_settle(shortcut(h))

        assert len(h.translator.calls) == 2
        assert [post["text"] for post in response_url_posts(h)] == [FAILED]

    async def test_the_note_is_shown_on_the_retry_path_before_the_result(self, h: Harness) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.translator.script = [RATE_LIMITED, BLUE_BOX]

        await h.deliver_and_settle(retry_payload(response_url=h.slack.response_url()))

        assert [post["text"] for post in response_url_posts(h)] == [
            "Translating…",
            "An error has occurred…",
            RESULT,
        ]
        h.slack.assert_no_public_writes()

    async def test_after_two_failures_the_note_is_followed_by_the_final_error(
        self, h: Harness
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.translator.script = [TIMEOUT]

        await h.deliver_and_settle(retry_payload(response_url=h.slack.response_url()))

        assert [post["text"] for post in response_url_posts(h)] == [
            "Translating…",
            "An error has occurred…",
            FAILED,
        ]

    async def test_a_note_that_cannot_be_delivered_does_not_stop_the_retry(
        self, h: Harness
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.slack.delay_next(RESPONSE_URL, 0)  # "Translating…"
        h.slack.fail_next(RESPONSE_URL, "expired_url")  # the note
        h.translator.script = [RATE_LIMITED, BLUE_BOX]

        await h.deliver_and_settle(retry_payload(response_url=h.slack.response_url()))

        assert len(h.translator.calls) == 2
        assert response_url_posts(h)[-1]["text"] == RESULT

    async def test_a_slow_note_never_delays_the_retry_or_replaces_the_result(
        self, h: Harness
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.slack.delay_next(RESPONSE_URL, 0)  # "Translating…"
        h.slack.delay_next(RESPONSE_URL, 0.8)  # the error note, slow
        started: list[float] = []

        async def mark() -> None:
            started.append(time.perf_counter())

        h.translator.on_call = mark
        h.translator.script = [RATE_LIMITED, BLUE_BOX]

        await h.deliver_and_settle(retry_payload(response_url=h.slack.response_url()))

        assert started[1] - started[0] < 0.5
        assert [post["text"] for post in response_url_posts(h)] == [
            "Translating…",
            "An error has occurred…",
            RESULT,
        ]
        h.slack.assert_no_public_writes()


ACTOR = "U0ACTOR"
HOME_MEMBER = {
    "team_id": "T0HOME",
    "is_bot": False,
    "deleted": False,
    "profile": {"email": "m1@x3mads.com"},
}


def click(h: Harness, listener: str, user_team: str | None) -> dict[str, Any]:
    url = h.slack.response_url()
    if listener == "translate_message":
        return shortcut_payload(user=ACTOR, user_team=user_team, response_url=url)
    return retry_payload(user=ACTOR, user_team=user_team, response_url=url)


@pytest.mark.parametrize("listener", ["translate_message", "translate_retry"])
class TestMembership:
    """CR-09 part A: every actor is looked up, and every refusal is silent and counted."""

    @pytest.mark.parametrize(
        ("payload_team", "record", "reason", "lookups"),
        [
            pytest.param(
                "T0OTHER", HOME_MEMBER, "workspace", 0, id="payload names another workspace"
            ),
            pytest.param(
                "T0HOME",
                {**HOME_MEMBER, "team_id": "T0OTHER"},
                "workspace",
                1,
                id="directory names another workspace",
            ),
            pytest.param(
                "T0HOME", {**HOME_MEMBER, "is_stranger": True}, "workspace", 1, id="stranger"
            ),
            pytest.param("T0HOME", {**HOME_MEMBER, "is_bot": True}, "workspace", 1, id="bot"),
            pytest.param(
                "T0HOME", {**HOME_MEMBER, "deleted": True}, "workspace", 1, id="deactivated"
            ),
            pytest.param(
                "T0HOME",
                {**HOME_MEMBER, "team_id": None},
                "unverified",
                1,
                id="no workspace in the directory",
            ),
            pytest.param(
                None, {**HOME_MEMBER, "team_id": None}, "unverified", 1, id="no workspace anywhere"
            ),
        ],
    )
    async def test_a_refused_actor_gets_nothing_and_is_counted_by_reason(
        self,
        h: Harness,
        listener: str,
        payload_team: str | None,
        record: dict[str, Any],
        reason: str,
        lookups: int,
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.slack.users[ACTOR] = {"id": ACTOR, **record}

        response = await h.deliver_and_settle(click(h, listener, payload_team))

        assert response is not None and response.status == 200
        assert h.classifier.calls == [] and h.translator.calls == []
        assert h.slack.calls_of_kind("private_write") == []
        assert h.slack.calls_of("conversations.history") == []
        assert len(h.slack.calls_of("users.info")) == lookups
        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == [reason]
        assert h.telemetry.named("translate_requested") == []

    async def test_a_person_the_directory_does_not_know_is_unverified(
        self, h: Harness, listener: str
    ) -> None:
        await h.deliver_and_settle(click(h, listener, "T0HOME"))
        assert h.slack.calls_of_kind("private_write") == []
        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == ["unverified"]

    async def test_a_member_is_looked_up_even_when_the_payload_names_the_home_workspace(
        self, h: Harness, listener: str
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.slack.users[ACTOR] = {"id": ACTOR, **HOME_MEMBER}

        await h.deliver_and_settle(click(h, listener, "T0HOME"))

        assert len(h.slack.calls_of("users.info")) == 1
        assert len(h.translator.calls) == 1
        assert h.telemetry.named("request_refused") == []

    async def test_a_lookup_that_fails_once_is_refused_and_the_next_click_is_served(
        self, h: Harness, listener: str
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.slack.users[ACTOR] = {"id": ACTOR, **HOME_MEMBER}
        h.slack.fail_next("users.info", "internal_error")

        await h.deliver_and_settle(click(h, listener, "T0HOME"))
        assert h.slack.calls_of_kind("private_write") == []
        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == ["unverified"]

        await h.deliver_and_settle(click(h, listener, "T0HOME"))
        assert len(h.translator.calls) == 1
        assert len(h.slack.calls_of("users.info")) == 2

    async def test_a_member_in_a_channel_outside_the_allowlist_gets_the_notice_and_is_counted(
        self, h: Harness, listener: str
    ) -> None:
        h.slack.users[ACTOR] = {"id": ACTOR, **HOME_MEMBER}
        payload = click(h, listener, "T0HOME")
        if listener == "translate_message":
            payload["channel"]["id"] = "C0OTHERXYZ"
        else:
            payload["channel"]["id"] = "C0OTHERXYZ"
            payload["container"]["channel_id"] = "C0OTHERXYZ"
            payload["actions"][0]["value"] = '{"v":1,"c":"C0OTHERXYZ","ts":"1795478400.000100"}'

        await h.deliver_and_settle(payload)

        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == ["channel"]
        assert h.translator.calls == []
        assert response_url_posts(h)[0]["text"] == (
            "X3M Translator is unavailable in this channel."
        )


@pytest.mark.parametrize("listener", ["translate_message", "translate_retry"])
class TestMembershipReview:
    """QA findings on CR-09 part A."""

    @pytest.mark.parametrize("team", [None, "", "   "], ids=["missing", "empty", "blank"])
    async def test_an_incomplete_lookup_is_not_cached_so_the_next_click_asks_again(
        self, h: Harness, listener: str, team: str | None
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        incomplete: dict[str, Any] = {"id": ACTOR, "is_bot": False, "deleted": False}
        if team is not None:
            incomplete["team_id"] = team
        h.slack.users[ACTOR] = incomplete

        await h.deliver_and_settle(click(h, listener, "T0HOME"))
        assert h.slack.calls_of_kind("private_write") == []
        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == ["unverified"]

        h.slack.users[ACTOR] = {"id": ACTOR, **HOME_MEMBER}
        await h.deliver_and_settle(click(h, listener, "T0HOME"))

        assert len(h.slack.calls_of("users.info")) == 2
        assert len(h.translator.calls) == 1

    async def test_a_complete_lookup_is_still_cached(self, h: Harness, listener: str) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.slack.users[ACTOR] = {"id": ACTOR, **HOME_MEMBER}
        await h.deliver_and_settle(click(h, listener, "T0HOME"))
        await h.deliver_and_settle(click(h, listener, "T0HOME"))
        assert len(h.slack.calls_of("users.info")) == 1

    async def test_a_refusal_never_names_the_person(self, h: Harness, listener: str) -> None:
        h.slack.users[ACTOR] = {"id": ACTOR, **HOME_MEMBER, "team_id": "T0OTHER"}
        await h.deliver_and_settle(click(h, listener, "T0HOME"))
        [event] = h.telemetry.named("request_refused")
        assert "user_id" not in event.fields
        assert ACTOR not in repr(event)

    async def test_an_error_from_the_directory_is_logged_by_class_and_counted_unverified(
        self, h: Harness, listener: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def explode(_: str) -> None:
            raise RuntimeError("secret profile marker")

        monkeypatch.setattr(h.app._gateway, "user_facts", explode)

        response = await h.deliver_and_settle(click(h, listener, "T0HOME"))

        assert response is not None and response.status == 200
        assert h.slack.calls_of_kind("private_write") == []
        assert h.translator.calls == []
        [error] = h.telemetry.named("internal_error")
        assert error.fields["exception_type"] == "RuntimeError"
        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == ["unverified"]
        assert "secret" not in repr(h.telemetry.events)


def with_email(address: str | None, **more: Any) -> dict[str, Any]:
    profile = {} if address is None else {"email": address}
    return {**HOME_MEMBER, "profile": profile, **more}


@pytest.mark.parametrize("listener", ["translate_message", "translate_retry"])
class TestEmailDomain:
    """CR-09 part C (decision 23): a member is in the X3M workspace with an X3M email."""

    @pytest.mark.parametrize(
        "record",
        [
            pytest.param(with_email("m1@x3mads.com"), id="x3mads"),
            pytest.param(with_email("m1@loomit.ai"), id="loomit"),
            pytest.param(with_email("M1@X3MADS.COM"), id="upper case"),
            pytest.param(
                with_email("m1@x3mads.com", is_restricted=True), id="guest with an X3M email"
            ),
            pytest.param(
                with_email("m1@x3mads.com", is_ultra_restricted=True), id="single-channel guest"
            ),
        ],
    )
    async def test_an_x3m_email_in_the_home_workspace_is_served(
        self, h: Harness, listener: str, record: dict[str, Any]
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.slack.users[ACTOR] = {"id": ACTOR, **record}

        await h.deliver_and_settle(click(h, listener, "T0HOME"))

        assert len(h.translator.calls) == 1
        assert h.telemetry.named("request_refused") == []

    @pytest.mark.parametrize(
        ("record", "reason"),
        [
            pytest.param(with_email("ana@gmail.com"), "email_domain", id="personal address"),
            pytest.param(with_email("a@x3mads.com.example.org"), "email_domain", id="suffix trick"),
            pytest.param(with_email("a@notx3mads.com"), "email_domain", id="prefix trick"),
            pytest.param(with_email("a@mail.x3mads.com"), "email_domain", id="subdomain"),
            pytest.param(
                with_email("a@gmail.com", is_restricted=True),
                "email_domain",
                id="guest, other email",
            ),
            pytest.param(with_email(None), "unverified", id="no email returned"),
            pytest.param(with_email("not-an-address"), "unverified", id="no usable address"),
            pytest.param(with_email("a@b@x3mads.com"), "unverified", id="two @ signs"),
        ],
    )
    async def test_anyone_else_gets_nothing_and_is_counted_by_reason(
        self, h: Harness, listener: str, record: dict[str, Any], reason: str
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.slack.users[ACTOR] = {"id": ACTOR, **record}

        response = await h.deliver_and_settle(click(h, listener, "T0HOME"))

        assert response is not None and response.status == 200
        assert h.classifier.calls == [] and h.translator.calls == []
        assert h.slack.calls_of_kind("private_write") == []
        assert h.slack.calls_of("conversations.history") == []
        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == [reason]


class TestScopeCheckAtStartup:
    async def test_the_bot_refuses_to_start_without_the_email_scope(self, h: Harness) -> None:
        h.slack.scopes = "commands,chat:write,users:read"
        with pytest.raises(MissingScope):
            await h.app.prepare()

    async def test_it_starts_when_the_scopes_are_listed(self, h: Harness) -> None:
        h.slack.scopes = "commands,users:read,users:read.email"
        await h.app.prepare()

    async def test_it_starts_when_slack_does_not_say_which_scopes_the_token_has(
        self, h: Harness
    ) -> None:
        h.slack.scopes = None
        await h.app.prepare()


class TestOffers:
    """The offer step through the real gateway and the fake Slack (FLOW-02 steps 5 to 7)."""

    def request(self, h: Harness, **overrides: Any) -> OfferRequest:
        values: dict[str, Any] = {
            "correlation_id": "c1",
            "ref": MessageRef(CHANNEL, TS, None),
            "author_id": "U0AUTHOR",
            "language": LanguageCode("ko"),
            "script": None,
            "received_at": h.clock.now(),
        }
        values.update(overrides)
        return OfferRequest(**values)

    def populate(self, h: Harness) -> None:
        for user in ("U0AUTHOR", "U0ONE", "U0TWO"):
            h.slack.add_user(user)
        h.slack.add_user("U0GUEST", profile={"email": "a@gmail.com"})
        h.slack.add_user("U0EXT", team_id="T0OTHER")
        h.slack.members[CHANNEL] = ["U0AUTHOR", "U0ONE", "U0TWO", "U0GUEST", "U0EXT"]

    async def test_members_get_private_offers_and_nobody_else_does(self, h: Harness) -> None:
        self.populate(h)

        await h.app.offers.send(self.request(h))

        offers = h.slack.calls_of("chat.postEphemeral")
        assert sorted(call.payload["user"] for call in offers) == ["U0ONE", "U0TWO"]
        assert all(call.payload["channel"] == CHANNEL for call in offers)
        assert (
            offers[0].payload["text"] == "Message in Korean. Translate this message into English."
        )
        assert "thread_ts" not in offers[0].payload
        assert h.translator.calls == []
        h.slack.assert_no_public_writes()
        [finished] = h.telemetry.named("offer_finished")
        assert (finished.fields["sent"], finished.fields["failed"]) == (2, 0)

    async def test_a_reply_is_offered_inside_its_thread(self, h: Harness) -> None:
        self.populate(h)
        ref = MessageRef(CHANNEL, "1795478400.000200", THREAD_ROOT)

        await h.app.offers.send(self.request(h, ref=ref))

        offers = h.slack.calls_of("chat.postEphemeral")
        assert offers and all(call.payload["thread_ts"] == THREAD_ROOT for call in offers)

    async def test_a_rate_limit_from_slack_is_waited_out_and_every_member_still_gets_the_offer(
        self, h: Harness
    ) -> None:
        self.populate(h)
        h.slack.fail_next("chat.postEphemeral", "ratelimited", status=429, retry_after=2)
        h.slack.fail_next("chat.postEphemeral", "ratelimited", status=429, retry_after=2)

        await h.app.offers.send(self.request(h))

        delivered = [c for c in h.slack.calls_of("chat.postEphemeral")]
        assert len(delivered) >= 2
        [finished] = h.telemetry.named("offer_finished")
        assert finished.fields["sent"] == 2
        assert any(abs(slept - 2.0) < 0.01 for slept in h.clock.slept)

    async def test_the_members_of_a_channel_are_listed_once_and_cached(self, h: Harness) -> None:
        self.populate(h)
        await h.app.offers.send(self.request(h))
        await h.app.offers.send(self.request(h, ref=MessageRef(CHANNEL, "1795478400.000300", None)))
        assert len(h.slack.calls_of("conversations.members")) == 1
        assert len(h.slack.calls_of("users.info")) == 4  # the author is never looked up


class TestOfferClick:
    """FLOW-03 · The click on a private offer: fetch, replace in place, no detection."""

    def offer(self, h: Harness, **overrides: Any) -> dict[str, Any]:
        return offer_payload(response_url=h.slack.response_url(), **overrides)

    async def test_the_click_translates_the_current_text_with_the_offered_language_as_the_hint(
        self, h: Harness
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)

        await h.deliver_and_settle(self.offer(h))

        assert h.classifier.calls == []
        [call] = h.translator.calls
        assert (call.text, call.target_language, call.source_language_hint) == (KOREAN, "en", "ko")
        assert [post["text"] for post in response_url_posts(h)] == [
            "Translating…",
            "English translation from Korean\nWhere is the blue box?",
        ]
        assert all(post["replace_original"] is True for post in response_url_posts(h))
        assert len(h.slack.calls_of("conversations.history")) == 1
        h.slack.assert_no_public_writes()
        assert [e.fields["entry"] for e in h.telemetry.named("translate_completed")] == ["offer"]

    async def test_an_edit_after_the_offer_is_translated_and_the_offered_language_is_kept(
        self, h: Harness
    ) -> None:
        h.slack.add_message(CHANNEL, TS, "Có ba ngôi sao giấy trong hộp màu xanh.")
        await h.deliver_and_settle(self.offer(h, language="ko"))
        [call] = h.translator.calls
        assert call.text.startswith("Có ba")
        assert call.source_language_hint == "ko"

    async def test_an_offer_on_a_thread_reply_reads_it_from_the_thread(self, h: Harness) -> None:
        reply = "1795478400.000200"
        h.slack.add_message(CHANNEL, THREAD_ROOT, "root")
        h.slack.add_message(CHANNEL, reply, KOREAN, thread_ts=THREAD_ROOT)
        await h.deliver_and_settle(self.offer(h, ts=reply, thread_root_ts=THREAD_ROOT))
        [fetch] = h.slack.calls_of("conversations.replies")
        assert fetch.payload["ts"] == THREAD_ROOT
        assert len(h.translator.calls) == 1

    async def test_a_message_deleted_after_the_offer_replaces_it_with_the_notice(
        self, h: Harness
    ) -> None:
        await h.deliver_and_settle(self.offer(h))
        assert h.translator.calls == []
        [post] = response_url_posts(h)
        assert post["text"] == "This message is no longer available."
        assert post["replace_original"] is True

    async def test_a_failed_translation_replaces_the_offer_with_the_error_and_a_retry_button(
        self, h: Harness
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.translator.script = [TIMEOUT, TIMEOUT]

        await h.deliver_and_settle(self.offer(h))

        texts = [post["text"] for post in response_url_posts(h)]
        assert texts == [
            "Translating…",
            "An error has occurred…",
            "We couldn’t translate this message. Retry translation.",
        ]
        retry = parse(button_value(response_url_posts(h)[-1]))
        assert retry.language is None, "Retry detects the language again"
        assert (retry.channel, retry.ts) == (CHANNEL, TS)

    async def test_one_failure_is_retried_and_the_note_comes_before_the_result(
        self, h: Harness
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.translator.script = [TIMEOUT, "Where is the blue box?"]
        await h.deliver_and_settle(self.offer(h))
        assert [post["text"] for post in response_url_posts(h)] == [
            "Translating…",
            "An error has occurred…",
            "English translation from Korean\nWhere is the blue box?",
        ]
        assert len(h.translator.calls) == 2

    async def test_an_expired_response_url_falls_back_to_a_private_message(
        self, h: Harness
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.slack.fail_next(RESPONSE_URL, "used_url", status=404)
        h.slack.fail_next(RESPONSE_URL, "used_url", status=404)
        await h.deliver_and_settle(self.offer(h))
        fallbacks = h.slack.calls_of("chat.postEphemeral")
        assert fallbacks and fallbacks[-1].payload["user"] == MEMBER
        h.slack.assert_no_public_writes()

    @pytest.mark.parametrize(
        ("team", "record"),
        [
            pytest.param("T0OTHER", HOME_MEMBER, id="another workspace"),
            pytest.param("T0HOME", with_email("a@gmail.com"), id="other email domain"),
            pytest.param("T0HOME", {**HOME_MEMBER, "is_stranger": True}, id="stranger"),
        ],
    )
    async def test_someone_who_is_not_a_member_gets_nothing_and_nothing_is_fetched(
        self, h: Harness, team: str, record: dict[str, Any]
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.slack.users[ACTOR] = {"id": ACTOR, **record}
        await h.deliver_and_settle(self.offer(h, user=ACTOR, user_team=team))
        assert h.slack.calls_of_kind("private_write") == []
        assert h.slack.calls_of("conversations.history") == []
        assert h.translator.calls == []

    async def test_a_channel_the_bot_has_left_replaces_the_offer_with_the_notice(
        self, h: Harness
    ) -> None:
        h.slack.add_channel(CHANNEL, is_member=False)
        h.app.served.forget(CHANNEL)
        await h.deliver_and_settle(self.offer(h))
        [post] = response_url_posts(h)
        assert post["text"] == "X3M Translator is unavailable in this channel."
        assert h.translator.calls == [] and h.slack.calls_of("conversations.history") == []

    async def test_a_tampered_offer_value_is_dropped_without_output(self, h: Harness) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        for bad in ("", "{}", '{"v":1,"c":"C0OTHERXYZ","ts":"1795478400.000100","l":"ko"}'):
            await h.deliver_and_settle(self.offer(h, value=bad))
        assert h.slack.calls_of_kind("private_write") == []
        assert h.translator.calls == []

    async def test_the_click_acknowledges_before_any_slow_work(self, h: Harness) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.translator.hold = asyncio.Event()
        response, elapsed = await h.deliver(self.offer(h))
        assert response is not None and response.status == 200
        assert elapsed < 1
        assert h.timeline[: h.timeline.index("telemetry:ack_observed")] == []
        h.translator.hold.set()
        await h.app.registry.drain()
