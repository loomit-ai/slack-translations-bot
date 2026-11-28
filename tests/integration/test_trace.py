"""LOG-04 · The steps a request takes, in order, with the values each step is allowed to carry."""

from typing import Any

import pytest
from tests.integration.conftest import Harness
from tests.support.event_payloads import message_event
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

from slack_translator.domain.detection import Detection
from slack_translator.domain.errors import ModelError, ModelErrorCategory
from slack_translator.domain.languages import LanguageCode

KOREAN = "파란 상자는 어디에 있나요?"
TIMEOUT = ModelError(ModelErrorCategory.TIMEOUT, "translate")
OTHER_CHANNEL = "C0OTHERXYZ"


def traces(h: Harness) -> list[dict[str, Any]]:
    return [event.fields for event in h.telemetry.named("trace")]


def steps(h: Harness) -> list[str]:
    return [fields["step"] for fields in traces(h)]


def step(h: Harness, name: str) -> dict[str, Any]:
    [found] = [fields for fields in traces(h) if fields["step"] == name]
    return found


def menu(h: Harness, **overrides: Any) -> dict[str, Any]:
    return shortcut_payload(response_url=h.slack.response_url(), **overrides)


def retry(h: Harness, **overrides: Any) -> dict[str, Any]:
    return retry_payload(response_url=h.slack.response_url(), **overrides)


def offer(h: Harness, **overrides: Any) -> dict[str, Any]:
    return offer_payload(response_url=h.slack.response_url(), **overrides)


MENU = [
    "received", "member_checked", "channel_checked", "admitted", "source_read",
    "detection_decided", "output_checked", "delivered_via",
]  # fmt: skip
CLICK = [
    "received", "member_checked", "channel_checked", "admitted", "source_read",
    "detection_decided", "progress_shown", "output_checked", "delivered_via",
]  # fmt: skip


class TestTheMessageMenu:
    async def test_a_plain_translation_writes_the_steps_in_order(self, h: Harness) -> None:
        await h.deliver_and_settle(menu(h, text=KOREAN))

        assert steps(h) == MENU
        assert step(h, "received")["entry"] == "shortcut"
        assert step(h, "member_checked")["outcome"] == "lookup"
        assert step(h, "channel_checked")["outcome"] == "cache", "startup remembered the channel"
        assert step(h, "source_read")["outcome"] == "payload"
        assert step(h, "source_read")["input_chars"] == len(KOREAN)
        decided = step(h, "detection_decided")
        assert (decided["outcome"], decided["language"]) == ("hinted", "ko")
        assert decided["confidence"] == 0.95
        assert step(h, "output_checked")["outcome"] == "unchanged"
        delivered = step(h, "delivered_via")
        assert (delivered["outcome"], delivered["slack_method"]) == ("ok", "response_url")

    async def test_a_second_click_by_the_same_person_finds_them_in_the_cache(
        self, h: Harness
    ) -> None:
        await h.deliver_and_settle(menu(h))
        h.telemetry.events.clear()
        await h.deliver_and_settle(menu(h))
        assert step(h, "member_checked")["outcome"] == "cache"

    async def test_a_channel_the_bot_must_ask_slack_about_is_a_lookup(self, h: Harness) -> None:
        h.app.served.forget(CHANNEL)
        await h.deliver_and_settle(menu(h))
        assert step(h, "channel_checked")["outcome"] == "lookup"

    async def test_a_retry_in_the_middle_adds_one_step_and_no_note(self, h: Harness) -> None:
        h.translator.script = [TIMEOUT, "Where is the blue box?"]
        await h.deliver_and_settle(menu(h))
        expected = [*MENU[:6], "retry_waited", *MENU[6:]]
        assert steps(h) == expected
        waited = step(h, "retry_waited")
        assert (waited["attempt"], waited["error_category"]) == (2, "timeout")
        assert waited["latency_ms"] >= 0

    @pytest.mark.parametrize(
        ("detection", "outcome"),
        [
            (Detection(LanguageCode("en"), 0.99), "already_read"),
            (Detection("other", 0.9), "unhinted"),
            (Detection(LanguageCode("ko"), 0.95), "hinted"),
        ],
    )
    async def test_the_detection_decision_names_what_was_decided(
        self, h: Harness, detection: Detection, outcome: str
    ) -> None:
        h.classifier.result = detection
        await h.deliver_and_settle(menu(h))
        assert step(h, "detection_decided")["outcome"] == outcome

    async def test_output_the_checker_had_to_escape_is_marked(self, h: Harness) -> None:
        h.translator.script = ["see <https://evil.example|this> and <!channel>"]
        await h.deliver_and_settle(menu(h, text="Bitte lesen Sie das hier"))
        assert step(h, "output_checked")["outcome"] == "escaped"

    @pytest.mark.parametrize(
        ("faults", "outcome", "method"),
        [
            ([], "ok", "response_url"),
            ([(RESPONSE_URL, "used_url", 404)], "fallback_ok", "chat.postEphemeral"),
            (
                [(RESPONSE_URL, "used_url", 404), ("chat.postEphemeral", "channel_not_found", 200)],
                "failed",
                None,
            ),
        ],
    )
    async def test_delivery_says_which_way_the_message_went_out(
        self, h: Harness, faults: list[tuple[str, str, int]], outcome: str, method: str | None
    ) -> None:
        for name, error, status in faults:
            h.slack.fail_next(name, error, status=status)
        await h.deliver_and_settle(menu(h))
        delivered = step(h, "delivered_via")
        assert delivered["outcome"] == outcome
        assert delivered.get("slack_method") == method


class TestFlowsThatStopEarly:
    async def test_a_click_naming_another_workspace_stops_after_the_member_check(
        self, h: Harness
    ) -> None:
        await h.deliver_and_settle(menu(h, user_team="T0OTHER"))
        assert steps(h) == ["received", "member_checked"]
        checked = step(h, "member_checked")
        assert (checked["outcome"], checked["refusal_reason"]) == ("payload", "workspace")

    async def test_a_member_in_a_channel_outside_the_list_stops_after_the_channel_check(
        self, h: Harness
    ) -> None:
        await h.deliver_and_settle(menu(h, channel=OTHER_CHANNEL))
        assert steps(h) == ["received", "member_checked", "channel_checked"]
        assert step(h, "channel_checked")["outcome"] == "list"

    async def test_a_message_that_is_gone_stops_after_the_read_and_says_so(
        self, h: Harness
    ) -> None:
        await h.deliver_and_settle(retry(h))
        read = step(h, "source_read")
        assert (read["outcome"], read["slack_method"]) == ("missing", "conversations.history")
        assert "detection_decided" not in steps(h)


class TestTheButtons:
    async def test_retry_click_writes_the_steps_with_progress_and_a_fetched_source(
        self, h: Harness
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        await h.deliver_and_settle(retry(h))

        assert steps(h) == CLICK
        assert step(h, "received")["entry"] == "retry"
        read = step(h, "source_read")
        assert (read["outcome"], read["slack_method"]) == ("fetched", "conversations.history")
        progress = step(h, "progress_shown")
        assert (progress["outcome"], progress["slack_method"]) == ("shown", "response_url")

    async def test_a_reply_is_read_from_its_thread(self, h: Harness) -> None:
        reply = "1795478400.000200"
        h.slack.add_message(CHANNEL, THREAD_ROOT, "root")
        h.slack.add_message(CHANNEL, reply, KOREAN, thread_ts=THREAD_ROOT)
        await h.deliver_and_settle(retry(h, ts=reply, thread_root_ts=THREAD_ROOT))
        assert step(h, "source_read")["slack_method"] == "conversations.replies"

    async def test_offer_click_decides_from_the_offer_and_a_retry_adds_the_note(
        self, h: Harness
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.translator.script = [TIMEOUT, "Where is the blue box?"]

        await h.deliver_and_settle(offer(h))

        assert steps(h) == [*CLICK[:7], "retry_waited", "retry_note_shown", *CLICK[7:]]
        assert step(h, "received")["entry"] == "offer"
        decided = step(h, "detection_decided")
        assert (decided["outcome"], decided["language"]) == ("from_offer", "ko")
        assert step(h, "retry_note_shown")["outcome"] == "shown"


class TestIntakeAndOffers:
    def populate(self, h: Harness) -> None:
        for user in ("U0ONE", "U0TWO"):
            h.slack.add_user(user)
        h.slack.members[CHANNEL] = ["U0ONE", "U0TWO"]

    async def test_a_customer_message_writes_received_accepted_decided_and_selected(
        self, h: Harness
    ) -> None:
        self.populate(h)
        await h.deliver_and_settle(message_event(text="파란 상자에 종이 별이 세 개 있어요."))

        assert steps(h) == ["received", "accepted", "detection_decided", "offers_selected"]
        assert step(h, "detection_decided")["outcome"] == "offer"
        selected = step(h, "offers_selected")
        assert (selected["recipient_count"], selected["language"]) == (2, "ko")
        assert selected["author_kind"] in {"x3m_member", "outside", "unverified"}

    async def test_a_message_that_is_not_in_a_customer_language_ends_with_no_offer(
        self, h: Harness
    ) -> None:
        self.populate(h)
        h.classifier.result = Detection(LanguageCode("en"), 0.99)
        await h.deliver_and_settle(message_event(text="I put three paper stars in the box."))
        assert steps(h) == ["received", "accepted", "detection_decided"]
        assert step(h, "detection_decided")["outcome"] == "no_offer"

    async def test_a_filtered_message_stops_after_received(self, h: Harness) -> None:
        await h.deliver_and_settle(message_event(bot_id="B0OTHERBOT"))
        assert steps(h) == ["received"]


class TestRateLimitWaits:
    async def test_a_wait_the_slack_adapter_makes_is_traced_with_its_length(
        self, h: Harness
    ) -> None:
        h.slack.fail_next("users.info", "ratelimited", status=429, retry_after=1)
        await h.deliver_and_settle(menu(h))
        waited = step(h, "rate_limit_waited")
        assert waited["slack_method"] == "users.info"
        assert waited["latency_ms"] == 1000


class TestNothingPrivateInATrace:
    async def test_no_trace_carries_a_person_a_workspace_or_text(self, h: Harness) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        h.slack.add_user("U0AUTHOR")
        h.slack.members[CHANNEL] = [MEMBER, "U0AUTHOR"]
        await h.deliver_and_settle(menu(h, text=KOREAN))
        await h.deliver_and_settle(retry(h))
        await h.deliver_and_settle(offer(h))
        await h.deliver_and_settle(message_event(text="파란 상자에 종이 별이 세 개 있어요."))

        assert traces(h)
        forbidden = {"user_id", "team_id", "exception_type", "logger", "text", "email"}
        for fields in traces(h):
            assert not forbidden & set(fields)
        everything = repr(traces(h))
        for marker in (MEMBER, "U0AUTHOR", "T0HOME", KOREAN, "x3mads.com", "hooks.slack"):
            assert marker not in everything, marker


class TestSlackFailuresCarryTheRequest:
    async def test_a_failed_slack_call_in_a_request_carries_its_correlation_id(
        self, h: Harness
    ) -> None:
        h.app.served.forget(CHANNEL)
        h.slack.fail_next("conversations.info", "internal_error")

        await h.deliver_and_settle(menu(h))

        [failure] = [
            e.fields
            for e in h.telemetry.named("slack_call")
            if e.fields["method"] == "conversations.info" and e.fields["outcome"] == "error"
        ]
        [refusal] = [e.fields for e in h.telemetry.named("request_refused")]
        assert failure["correlation_id"] == refusal["correlation_id"] is not None

    @pytest.mark.parametrize(
        "h", [{"CHANNEL_ALLOWLIST": "", "APP_ENV": "production"}], indirect=True
    )
    async def test_a_failed_slack_call_at_startup_has_none(self, h: Harness) -> None:
        h.slack.fail_next("users.conversations", "internal_error")
        h.telemetry.events.clear()

        await h.app.prepare()

        failures = [
            e.fields for e in h.telemetry.named("slack_call") if e.fields["outcome"] != "ok"
        ]
        assert failures
        assert all(f["correlation_id"] is None for f in failures)
