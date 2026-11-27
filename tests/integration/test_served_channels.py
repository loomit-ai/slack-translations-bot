"""CR-11 (decision 25) · A channel is served when the bot is a member of it."""

from typing import Any

import pytest
from tests.integration.conftest import Harness
from tests.support.payloads import CHANNEL, MEMBER, TS, retry_payload, shortcut_payload

NO_LIST = {"CHANNEL_ALLOWLIST": "", "APP_ENV": "production"}
OTHER = "C0OTHERXYZ"
UNAVAILABLE = "Loomit Translator is unavailable in this channel."
KOREAN = "파란 상자는 어디에 있나요?"


def click(h: Harness, listener: str, channel: str) -> dict[str, Any]:
    url = h.slack.response_url()
    if listener == "translate_message":
        return shortcut_payload(channel=channel, response_url=url)
    return retry_payload(channel=channel, response_url=url)


class TestIsServed:
    async def test_a_channel_the_bot_is_in_is_served_and_one_it_is_not_in_is_not(
        self, h: Harness
    ) -> None:
        h.slack.add_channel("C0MEMBERXYZ", is_member=True)
        h.slack.add_channel("C0STRANGERX", is_member=False)
        served = h.app.served
        assert await served.is_served("C0MEMBERXYZ") is True
        assert await served.is_served("C0STRANGERX") is False

    async def test_a_channel_slack_will_not_show_is_not_served(self, h: Harness) -> None:
        assert await h.app.served.is_served("C0UNKNOWN99") is False

    async def test_yes_is_remembered_for_ten_minutes(self, h: Harness) -> None:
        h.app.served.forget(CHANNEL)  # startup had already remembered it
        await h.app.served.is_served(CHANNEL)
        h.clock.advance(599)
        await h.app.served.is_served(CHANNEL)
        assert len(h.slack.calls_of("conversations.info")) == 1
        h.clock.advance(2)
        await h.app.served.is_served(CHANNEL)
        assert len(h.slack.calls_of("conversations.info")) == 2

    async def test_no_is_remembered_for_one_minute(self, h: Harness) -> None:
        h.slack.add_channel("C0STRANGERX", is_member=False)
        await h.app.served.is_served("C0STRANGERX")
        h.clock.advance(59)
        await h.app.served.is_served("C0STRANGERX")
        assert len(h.slack.calls_of("conversations.info")) == 1
        h.clock.advance(2)
        h.slack.add_channel("C0STRANGERX", is_member=True)
        assert await h.app.served.is_served("C0STRANGERX") is True

    async def test_an_error_means_not_served_and_is_not_remembered(self, h: Harness) -> None:
        h.app.served.forget(CHANNEL)
        h.slack.fail_next("conversations.info", "internal_error")
        assert await h.app.served.is_served(CHANNEL) is False
        assert await h.app.served.is_served(CHANNEL) is True

    async def test_an_event_from_a_channel_marks_it_served_without_asking_slack(
        self, h: Harness
    ) -> None:
        h.slack.add_channel("C0NEWLYADD", is_member=False)
        h.app.served.note_event_from("C0NEWLYADD")
        assert await h.app.served.is_served("C0NEWLYADD") is True
        assert h.slack.calls_of("conversations.info") == []

    async def test_a_channel_the_bot_left_is_forgotten(self, h: Harness) -> None:
        h.app.served.note_event_from(CHANNEL)
        h.slack.add_channel(CHANNEL, is_member=False)
        h.app.served.forget(CHANNEL)
        assert await h.app.served.is_served(CHANNEL) is False


@pytest.mark.parametrize("listener", ["translate_message", "translate_retry"])
class TestMenuAndRetry:
    async def test_a_member_in_a_channel_the_bot_is_in_is_served(
        self, h: Harness, listener: str
    ) -> None:
        h.slack.add_message(CHANNEL, TS, KOREAN)
        await h.deliver_and_settle(click(h, listener, CHANNEL))
        assert len(h.translator.calls) == 1

    async def test_a_channel_the_bot_has_not_been_added_to_gets_the_notice_and_no_model_call(
        self, h: Harness, listener: str
    ) -> None:
        h.slack.add_channel(CHANNEL, is_member=False)
        h.app.served.forget(CHANNEL)  # startup had remembered it from before
        h.slack.add_message(CHANNEL, TS, KOREAN)

        await h.deliver_and_settle(click(h, listener, CHANNEL))

        assert h.translator.calls == [] and h.classifier.calls == []
        assert h.slack.calls_of("conversations.history") == []
        [post] = [call.payload for call in h.slack.calls_of("response_url")]
        assert post["text"] == UNAVAILABLE
        assert post["blocks"][0]["text"]["type"] == "plain_text"
        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == ["channel"]

    async def test_a_channel_outside_the_list_is_refused_even_if_the_bot_is_in_it(
        self, h: Harness, listener: str
    ) -> None:
        h.slack.add_channel(OTHER, is_member=True)
        await h.deliver_and_settle(click(h, listener, OTHER))
        assert h.translator.calls == []
        assert h.slack.calls_of("conversations.info") == []
        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == ["channel"]

    async def test_slack_not_being_able_to_say_means_the_notice(
        self, h: Harness, listener: str
    ) -> None:
        h.app.served.forget(CHANNEL)
        h.slack.fail_next("conversations.info", "internal_error")
        await h.deliver_and_settle(click(h, listener, CHANNEL))
        assert h.translator.calls == []
        assert [e.fields["reason"] for e in h.telemetry.named("request_refused")] == ["channel"]

    async def test_someone_who_is_not_a_member_gets_nothing_whatever_the_channel(
        self, h: Harness, listener: str
    ) -> None:
        h.slack.add_channel(CHANNEL, is_member=False)
        payload = click(h, listener, CHANNEL)
        payload["user"]["team_id"] = "T0OTHER"
        await h.deliver_and_settle(payload)
        assert h.slack.calls_of_kind("private_write") == []
        assert h.slack.calls_of("conversations.info") == []

    @pytest.mark.parametrize("h", [NO_LIST], indirect=True)
    async def test_with_no_list_membership_alone_decides(self, h: Harness, listener: str) -> None:
        h.slack.add_channel(OTHER, is_member=True)
        h.slack.add_message(OTHER, TS, KOREAN)
        await h.deliver_and_settle(click(h, listener, OTHER))
        assert len(h.translator.calls) == 1

    async def test_a_refusal_does_not_say_how_to_add_the_bot(
        self, h: Harness, listener: str
    ) -> None:
        h.slack.add_channel(CHANNEL, is_member=False)
        h.app.served.forget(CHANNEL)
        await h.deliver_and_settle(click(h, listener, CHANNEL))
        [post] = [call.payload for call in h.slack.calls_of("response_url")]
        assert post["text"] == UNAVAILABLE == post["blocks"][0]["text"]["text"]
        assert len(post["blocks"]) == 1


def event(kind: str, user: str, channel: str = CHANNEL, **more: Any) -> dict[str, Any]:
    return {
        "type": "event_callback",
        "team_id": "T0HOME",
        "event_id": f"Ev-{kind}-{user}",
        "event": {"type": kind, "user": user, "channel": channel, **more},
    }


class TestStartup:
    async def test_with_a_list_each_listed_channel_the_bot_is_in_is_remembered_and_others_warned(
        self, h: Harness
    ) -> None:
        h.slack.add_channel(CHANNEL, is_member=False)
        await h.app.prepare()
        assert [
            e.fields["channel_id"] for e in h.telemetry.named("startup_channel_not_member")
        ] == [CHANNEL]

        h.slack.add_channel(CHANNEL, is_member=True)
        h.slack.calls.clear()
        await h.app.prepare()
        assert await h.app.served.is_served(CHANNEL) is True
        assert h.slack.calls_of("users.conversations") == []

    @pytest.mark.parametrize("h", [NO_LIST], indirect=True)
    async def test_with_no_list_the_bot_learns_the_channels_it_is_in(self, h: Harness) -> None:
        h.slack.channels.clear()
        h.slack.add_channel("C0ONEXXXXX", is_member=True)
        h.slack.add_channel("G0TWOXXXXX", is_member=True)
        h.slack.add_channel("C0NOTINXXX", is_member=False)
        h.telemetry.events.clear()
        h.slack.calls.clear()

        await h.app.prepare()

        assert [e.fields["channel_count"] for e in h.telemetry.named("startup_serving")] == [2]
        assert h.telemetry.named("startup_channel_not_member") == []
        h.slack.calls.clear()
        assert await h.app.served.is_served("C0ONEXXXXX") is True
        assert await h.app.served.is_served("G0TWOXXXXX") is True
        assert h.slack.calls_of("conversations.info") == []

    @pytest.mark.parametrize("h", [NO_LIST], indirect=True)
    async def test_a_failed_listing_is_not_fatal(self, h: Harness) -> None:
        h.slack.fail_next("users.conversations", "internal_error")
        h.telemetry.events.clear()
        await h.app.prepare()
        assert h.telemetry.named("startup_serving") == []

    @pytest.mark.parametrize("h", [NO_LIST], indirect=True)
    async def test_starting_with_no_channels_is_not_an_error(self, h: Harness) -> None:
        h.slack.channels.clear()
        h.telemetry.events.clear()
        await h.app.prepare()
        assert [e.fields["channel_count"] for e in h.telemetry.named("startup_serving")] == [0]


class TestJoinAndLeave:
    async def test_the_bot_being_added_makes_the_channel_served_and_leaves_an_audit_line(
        self, h: Harness
    ) -> None:
        h.slack.add_channel("C0NEWLYADD", is_member=False)

        await h.deliver_and_settle(
            event("member_joined_channel", "U0BOT", "C0NEWLYADD", inviter="U0INV")
        )

        assert await h.app.served.is_served("C0NEWLYADD") is True
        [change] = h.telemetry.named("channel_changed")
        assert change.fields == {"change": "joined", "channel_id": "C0NEWLYADD", "user_id": "U0INV"}

    async def test_the_bot_leaving_makes_the_channel_unserved(self, h: Harness) -> None:
        h.app.served.note_event_from(CHANNEL)
        h.slack.add_channel(CHANNEL, is_member=False)

        await h.deliver_and_settle(event("member_left_channel", "U0BOT"))

        assert await h.app.served.is_served(CHANNEL) is False
        [change] = h.telemetry.named("channel_changed")
        assert change.fields["change"] == "left"

    @pytest.mark.parametrize("kind", ["member_joined_channel", "member_left_channel"])
    async def test_other_peoples_joins_and_leaves_are_ignored_without_a_trace(
        self, h: Harness, kind: str
    ) -> None:
        h.app.served.note_event_from(CHANNEL)
        h.slack.add_channel(CHANNEL, is_member=False)

        await h.deliver_and_settle(event(kind, MEMBER))

        assert h.telemetry.named("channel_changed") == []
        assert await h.app.served.is_served(CHANNEL) is True
