"""FLOW-02 through `create_app`: message events are filtered, classified, counted and offered."""

import asyncio
from typing import Any

import pytest
from tests.integration.conftest import Harness
from tests.support.event_payloads import message_event
from tests.support.payloads import CHANNEL, MEMBER, TS

from slack_translator.domain.detection import Detection
from slack_translator.domain.errors import ModelError, ModelErrorCategory
from slack_translator.domain.languages import LanguageCode

KOREAN = "파란 상자에 종이 별이 세 개 있어요."
NO_LIST = {"CHANNEL_ALLOWLIST": "", "APP_ENV": "production"}


def korean(confidence: float = 0.95) -> Detection:
    return Detection(LanguageCode("ko"), confidence)


def classified(h: Harness) -> list[dict[str, Any]]:
    return [e.fields for e in h.telemetry.named("classify_completed")]


def dropped(h: Harness) -> list[str]:
    return [e.fields["reason"] for e in h.telemetry.named("event_dropped")]


def offers(h: Harness) -> list[dict[str, Any]]:
    return [call.payload for call in h.slack.calls_of("chat.postEphemeral")]


def with_members(h: Harness, *users: str) -> None:
    for user in users:
        h.slack.add_user(user)
    h.slack.members[CHANNEL] = list(users)


class TestAConfidentCustomerMessage:
    async def test_every_x3m_member_but_the_customer_gets_one_private_offer(
        self, h: Harness
    ) -> None:
        with_members(h, "U0MEMBER1", "U0MEMBER2", "U0MEMBER3")
        h.classifier.result = korean()

        await h.deliver_and_settle(message_event(text=KOREAN, user="U0CUSTOMER"))

        assert h.classifier.calls == [KOREAN]
        assert sorted(offer["user"] for offer in offers(h)) == [
            "U0MEMBER1",
            "U0MEMBER2",
            "U0MEMBER3",
        ]
        assert classified(h)[0]["result"] == "customer"
        h.slack.assert_no_public_writes()
        assert h.translator.calls == []

    async def test_an_x3m_member_who_writes_in_a_customer_language_gets_no_offer_for_it(
        self, h: Harness
    ) -> None:
        with_members(h, "U0MEMBER1", "U0MEMBER2")
        h.classifier.result = korean()

        await h.deliver_and_settle(message_event(text=KOREAN, user="U0MEMBER1"))

        assert [offer["user"] for offer in offers(h)] == ["U0MEMBER2"]

    async def test_a_reply_in_a_thread_is_offered_inside_that_thread(self, h: Harness) -> None:
        with_members(h, "U0MEMBER1")
        h.classifier.result = korean()

        await h.deliver_and_settle(message_event(thread_ts="1795478000.000050"))

        [offer] = offers(h)
        assert offer["thread_ts"] == "1795478000.000050"

    async def test_slack_gets_its_acknowledgement_before_any_classification(
        self, h: Harness
    ) -> None:
        h.classifier.hold = asyncio.Event()

        response, seconds = await h.deliver(message_event())

        assert response is not None and response.status == 200
        assert seconds < 1.0
        h.classifier.hold.set()
        await h.app.registry.drain()


class TestEverythingElseStopsQuietly:
    @pytest.mark.parametrize(
        ("kwargs", "reason"),
        [
            ({"channel": "C0OTHERXYZ"}, "not_allowlisted"),
            ({"channel": "D0DIRECTMSG", "channel_type": "im"}, "not_allowlisted"),
            ({"subtype": "message_changed"}, "subtype"),
            ({"subtype": "message_deleted", "text": None, "user": None}, "subtype"),
            ({"subtype": "channel_join"}, "subtype"),
            ({"bot_id": "B0OTHERBOT", "user": None}, "bot"),
            ({"text": None}, "empty"),
            ({"text": "   "}, "empty"),
            ({"text": "<@U0MEMBER1> <https://example.com|link> :tada: `x`"}, "too_short"),
            ({"text": "ok"}, "too_short"),
        ],
    )
    async def test_an_excluded_message_costs_no_classification_and_no_slack_call(
        self, h: Harness, kwargs: dict[str, Any], reason: str
    ) -> None:
        with_members(h, "U0MEMBER1")
        h.slack.calls.clear()

        await h.deliver_and_settle(message_event(**kwargs))

        assert h.classifier.calls == []
        assert dropped(h) == [reason]
        assert h.slack.calls == []
        assert classified(h) == []

    @pytest.mark.parametrize(
        ("detection", "result", "language"),
        [
            (Detection(LanguageCode("en"), 0.97), "team", "en"),
            (Detection(LanguageCode("es"), 0.9), "team", "es"),
            (Detection("other", 0.95), "other", "other"),
            (Detection(LanguageCode("ko"), 0.55), "low_confidence", "ko"),
        ],
    )
    async def test_a_message_that_is_not_in_a_customer_language_is_counted_and_not_offered(
        self, h: Harness, detection: Detection, result: str, language: str
    ) -> None:
        with_members(h, "U0MEMBER1")
        h.classifier.result = detection
        h.slack.calls.clear()

        await h.deliver_and_settle(message_event(text="Some made-up words to classify here"))

        [counted] = classified(h)
        assert (counted["result"], counted["language"]) == (result, language)
        assert h.slack.calls == []

    @pytest.mark.parametrize(
        "failure",
        [
            ModelError(ModelErrorCategory.TIMEOUT, "detect"),
            ModelError(ModelErrorCategory.INVALID_RESPONSE, "detect"),
            TimeoutError(),
        ],
    )
    async def test_a_classifier_failure_is_counted_and_the_member_sees_nothing(
        self, h: Harness, failure: Exception
    ) -> None:
        with_members(h, "U0MEMBER1")
        h.classifier.result = failure
        h.slack.calls.clear()

        await h.deliver_and_settle(message_event())

        assert [c["result"] for c in classified(h)] == ["error"]
        assert h.slack.calls == []
        assert h.telemetry.named("internal_error") == []

    async def test_a_classifier_that_hangs_is_cut_off_at_its_deadline(self, h: Harness) -> None:
        with_members(h, "U0MEMBER1")
        h.classifier.hold = asyncio.Event()
        h.slack.calls.clear()

        await h.deliver_and_settle(message_event())

        assert [c["result"] for c in classified(h)] == ["error"]
        assert h.slack.calls == []


class TestDuplicates:
    async def test_an_event_delivered_twice_in_a_row_is_offered_once(self, h: Harness) -> None:
        with_members(h, "U0MEMBER1", "U0MEMBER2")
        h.classifier.result = korean()

        await h.deliver_and_settle(message_event(event_id="Ev1"))
        await h.deliver_and_settle(message_event(event_id="Ev1"))

        assert len(h.classifier.calls) == 1
        assert len(offers(h)) == 2
        assert dropped(h) == ["duplicate"]

    async def test_an_event_delivered_twice_at_once_is_offered_once(self, h: Harness) -> None:
        with_members(h, "U0MEMBER1", "U0MEMBER2")
        h.classifier.result = korean()
        h.classifier.hold = asyncio.Event()

        await asyncio.gather(
            h.deliver(message_event(event_id="Ev1")), h.deliver(message_event(event_id="Ev1"))
        )
        await asyncio.sleep(0.05)
        h.classifier.hold.set()
        await h.app.registry.drain()

        assert len(h.classifier.calls) == 1
        assert len(offers(h)) == 2
        assert dropped(h) == ["duplicate"]

    async def test_a_new_event_id_is_a_new_message(self, h: Harness) -> None:
        with_members(h, "U0MEMBER1")
        h.classifier.result = korean()

        await h.deliver_and_settle(message_event(event_id="Ev1", ts=TS))
        await h.deliver_and_settle(message_event(event_id="Ev2", ts="1795478401.000200"))

        assert len(h.classifier.calls) == 2
        assert len(offers(h)) == 2


class TestWithNoChannelList:
    @pytest.mark.parametrize("h", [NO_LIST], indirect=True)
    async def test_an_event_from_any_channel_is_classified_and_marks_it_served(
        self, h: Harness
    ) -> None:
        h.classifier.result = Detection(LanguageCode("en"), 0.97)
        h.slack.calls.clear()

        await h.deliver_and_settle(message_event(channel="C0NEWCHANNEL"))

        assert len(h.classifier.calls) == 1
        assert h.slack.calls == []
        assert await h.app.served.is_served("C0NEWCHANNEL") is True
        assert h.slack.calls_of("conversations.info") == []

    @pytest.mark.parametrize("h", [NO_LIST], indirect=True)
    async def test_a_direct_message_is_dropped_and_its_channel_is_not_served(
        self, h: Harness
    ) -> None:
        await h.deliver_and_settle(message_event(channel="D0DIRECTMSG", channel_type="im"))

        assert h.classifier.calls == []
        assert dropped(h) == ["not_allowlisted"]
        assert await h.app.served.is_served("D0DIRECTMSG") is False


async def test_the_author_is_never_looked_up_before_classification(h: Harness) -> None:
    h.classifier.result = Detection(LanguageCode("en"), 0.97)
    h.slack.calls.clear()

    await h.deliver_and_settle(message_event(user=MEMBER))

    assert h.slack.calls_of("users.info") == []
