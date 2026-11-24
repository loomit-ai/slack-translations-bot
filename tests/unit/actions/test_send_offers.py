"""FLOW-02 steps 5 to 7 · One private offer per eligible member, paced, cut off at a minute."""

from typing import Any

import pytest
from tests.support.fake_ports import FakeClock, FakeDirectory, FakeResponder, RecordingTelemetry

from slack_translator.actions.send_offers import MAX_IN_FLIGHT, OFFER_LIFETIME, SendOffers
from slack_translator.actions.translate_message import HomeWorkspace, TranslationPolicy
from slack_translator.delivery.views.presenter import SlackReadPresenter
from slack_translator.domain.action_values import parse
from slack_translator.domain.errors import SlackDeliveryError
from slack_translator.domain.languages import LanguageCode, parse_language_seed
from slack_translator.domain.messages import MessageRef, OfferRequest, UserFacts

CHANNEL = "C0123ABCDE"
TS = "1795478400.000100"
ROOT = "1795478000.000050"
LANGUAGES = parse_language_seed("en:team,es:team,ko:customer,zh:customer")


def member(**overrides: Any) -> UserFacts:
    values: dict[str, Any] = {
        "team_id": "T0HOME",
        "is_bot": False,
        "deleted": False,
        "is_stranger": False,
        "email_domain": "x3mads.com",
    }
    values.update(overrides)
    return UserFacts(**values)


def policy() -> TranslationPolicy:
    return TranslationPolicy(
        allowlist=(CHANNEL,),
        languages=LANGUAGES,
        read_language=LanguageCode("en"),
        min_confidence=0.7,
        classifier_timeout=3,
        translator_timeout=20,
        member_email_domains=frozenset({"x3mads.com", "loomit.ai"}),
    )


class Rig:
    def __init__(self, members: list[str], facts: dict[str, Any]) -> None:
        self.clock = FakeClock()
        self.directory = FakeDirectory(members, facts)
        self.responder = FakeResponder()
        self.telemetry = RecordingTelemetry()
        home = HomeWorkspace()
        home.team_id = "T0HOME"
        self.offers = SendOffers(
            policy=policy(),
            home=home,
            directory=self.directory,
            responder=self.responder,
            presenter=SlackReadPresenter("English"),
            telemetry=self.telemetry,
            clock=self.clock,
        )

    def request(self, **overrides: Any) -> OfferRequest:
        values: dict[str, Any] = {
            "correlation_id": "c1",
            "ref": MessageRef(CHANNEL, TS, None),
            "author_id": "U0AUTHOR",
            "language": LanguageCode("ko"),
            "script": None,
            "received_at": self.clock.now(),
        }
        values.update(overrides)
        return OfferRequest(**values)

    def finished(self) -> dict[str, Any]:
        [event] = self.telemetry.named("offer_finished")
        return event.fields

    def offered_users(self) -> list[str]:
        return sorted(call.user for call in self.responder.calls)


def rig_with(users: dict[str, Any], author: str = "U0AUTHOR") -> Rig:
    return Rig([author, *users], {author: member(), **users})


async def test_every_eligible_member_gets_one_offer_and_the_author_gets_none() -> None:
    rig = rig_with({"U1": member(), "U2": member(email_domain="loomit.ai")})
    await rig.offers.send(rig.request())
    assert rig.offered_users() == ["U1", "U2"]
    assert rig.directory.lookups.count("U0AUTHOR") == 1, "once, for counting only"
    assert rig.finished()["sent"] == 2


@pytest.mark.parametrize(
    "facts",
    [
        member(team_id="T0OTHER"),
        member(is_stranger=True),
        member(is_bot=True),
        member(deleted=True),
        member(email_domain="gmail.com"),
        member(email_domain=None),
        member(team_id=None),
        None,
        RuntimeError("lookup failed"),
    ],
    ids=[
        "other workspace",
        "stranger",
        "bot",
        "deactivated",
        "other email",
        "no email",
        "no workspace",
        "unknown",
        "lookup error",
    ],
)
async def test_nobody_else_gets_an_offer(facts: Any) -> None:
    rig = rig_with({"U1": member(), "U2": facts})
    await rig.offers.send(rig.request())
    assert rig.offered_users() == ["U1"]


async def test_a_message_without_a_known_author_is_offered_to_every_member() -> None:
    rig = rig_with({"U1": member()})
    await rig.offers.send(rig.request(author_id=None))
    assert rig.offered_users() == ["U0AUTHOR", "U1"]


async def test_an_unknown_workspace_means_nobody_is_offered_anything() -> None:
    rig = rig_with({"U1": member()})
    rig.offers._home.team_id = None
    await rig.offers.send(rig.request())
    assert rig.responder.calls == []


async def test_the_offer_names_the_language_and_carries_what_the_click_needs() -> None:
    rig = rig_with({"U1": member()})
    await rig.offers.send(rig.request(language=LanguageCode("zh"), script="Hant"))

    [call] = rig.responder.calls
    assert call.text == "Message in Chinese. Translate this message into English."
    [_, actions] = call.blocks
    [button] = actions["elements"]
    assert button["action_id"] == "translate_offer"
    value = parse(button["value"])
    assert (value.channel, value.ts, value.thread_root_ts) == (CHANNEL, TS, None)
    assert (value.language, value.script) == ("zh", "Hant")


@pytest.mark.parametrize(("root", "expected"), [(None, None), (ROOT, ROOT)])
async def test_a_reply_is_offered_inside_its_thread(root: str | None, expected: str | None) -> None:
    rig = rig_with({"U1": member()})
    await rig.offers.send(rig.request(ref=MessageRef(CHANNEL, TS, root)))
    [call] = rig.responder.calls
    assert call.thread_ts == expected
    assert parse(call.blocks[1]["elements"][0]["value"]).thread_root_ts == expected


async def test_at_most_four_offers_and_four_lookups_are_in_flight() -> None:
    users = {f"U{i}": member() for i in range(12)}
    rig = rig_with(users)
    await rig.offers.send(rig.request())
    assert len(rig.responder.calls) == 12
    assert MAX_IN_FLIGHT == 4
    assert rig.responder.max_in_flight <= MAX_IN_FLIGHT
    assert rig.directory.max_in_flight <= MAX_IN_FLIGHT


async def test_a_rate_limit_makes_every_offer_wait_for_the_retry_after_then_go_out() -> None:
    rig = rig_with({"U1": member(), "U2": member(), "U3": member()})
    rig.responder.script["U1"] = [SlackDeliveryError("ratelimited", retry_after=3.0)]

    await rig.offers.send(rig.request())

    assert rig.offered_users() == ["U1", "U2", "U3"]
    assert any(abs(slept - 3.0) < 0.01 for slept in rig.clock.slept)
    assert rig.finished() == {
        "correlation_id": "c1", "channel_id": CHANNEL, "message_ts": TS,
        "sent": 3, "failed": 0, "expired": 0, "author_kind": "x3m_member",
    }  # fmt: skip


async def test_no_offer_goes_out_after_the_cut_off_even_after_a_rate_limit_wait() -> None:
    rig = rig_with({"U1": member()})
    request = rig.request(received_at=rig.clock.now() - 58)
    rig.responder.script["U1"] = [SlackDeliveryError("ratelimited", retry_after=5.0)]

    await rig.offers.send(request)

    assert rig.responder.calls == []
    assert rig.finished()["expired"] == 1
    assert rig.clock.now() <= request.received_at + OFFER_LIFETIME


async def test_an_offer_that_cannot_go_out_within_a_minute_of_the_event_expires() -> None:
    rig = rig_with({"U1": member(), "U2": member()})
    rig.clock.advance(40)
    rig.responder.script["U1"] = [SlackDeliveryError("ratelimited", retry_after=30.0)]
    request = rig.request(received_at=rig.clock.now() - 40)

    await rig.offers.send(request)

    assert rig.finished()["expired"] >= 1
    assert rig.finished()["sent"] + rig.finished()["expired"] == 2


async def test_an_event_older_than_a_minute_sends_nothing() -> None:
    rig = rig_with({"U1": member(), "U2": member()})
    await rig.offers.send(rig.request(received_at=rig.clock.now() - OFFER_LIFETIME - 1))
    assert rig.responder.calls == []
    assert rig.finished()["expired"] == 2
    assert rig.finished()["sent"] == 0


async def test_a_refusal_from_slack_is_counted_as_failed_and_the_others_still_go_out() -> None:
    rig = rig_with({"U1": member(), "U2": member()})
    rig.responder.script["U1"] = [SlackDeliveryError("channel_not_found")]
    await rig.offers.send(rig.request())
    assert rig.offered_users() == ["U2"]
    assert (rig.finished()["sent"], rig.finished()["failed"]) == (1, 1)


async def test_a_channel_whose_members_cannot_be_listed_gets_no_offers_and_no_crash() -> None:
    rig = rig_with({"U1": member()})
    rig.directory.listing_error = SlackDeliveryError("ratelimited")
    await rig.offers.send(rig.request())
    assert rig.responder.calls == []
    assert [e.fields["exception_type"] for e in rig.telemetry.named("internal_error")] == [
        "SlackDeliveryError"
    ]


async def test_send_never_raises_even_when_the_responder_breaks() -> None:
    rig = rig_with({"U1": member()})
    rig.responder.script["U1"] = [RuntimeError("boom with secret text")]
    await rig.offers.send(rig.request())
    assert rig.finished()["failed"] == 1
    assert "secret" not in repr(rig.telemetry.events)


async def test_the_events_carry_counts_not_people_or_text() -> None:
    rig = rig_with({"U1": member()})
    await rig.offers.send(rig.request())
    assert "U1" not in repr(rig.telemetry.events)
    assert "U0AUTHOR" not in repr(rig.telemetry.events)


class TestAuthorKind:
    """F2 · Offers are counted by who wrote the message: an X3M member, an outsider, or unknown."""

    @pytest.mark.parametrize(
        ("author_facts", "kind"),
        [
            pytest.param(member(), "x3m_member", id="colleague"),
            pytest.param(
                member(email_domain="loomit.ai"), "x3m_member", id="colleague, other X3M domain"
            ),
            pytest.param(member(team_id="T0OTHER"), "outside", id="Slack Connect customer"),
            pytest.param(member(is_stranger=True), "outside", id="stranger"),
            pytest.param(
                member(email_domain="gmail.com"), "outside", id="home workspace, other email"
            ),
            pytest.param(member(is_bot=True), "outside", id="bot"),
            pytest.param(None, "unverified", id="unknown author"),
            pytest.param(member(team_id=None), "unverified", id="no workspace"),
            pytest.param(member(email_domain=None), "unverified", id="no email"),
            pytest.param(RuntimeError("lookup failed"), "unverified", id="lookup error"),
        ],
    )
    async def test_the_author_is_classified_once_with_the_membership_rule(
        self, author_facts: Any, kind: str
    ) -> None:
        rig = Rig(["U0AUTHOR", "U1"], {"U0AUTHOR": author_facts, "U1": member()})

        await rig.offers.send(rig.request())

        assert rig.finished()["author_kind"] == kind
        assert rig.directory.lookups.count("U0AUTHOR") == 1
        assert rig.offered_users() == ["U1"], "offers go out whatever the author is"

    async def test_a_message_without_a_known_author_is_unverified(self) -> None:
        rig = rig_with({"U1": member()})
        await rig.offers.send(rig.request(author_id=None))
        assert rig.finished()["author_kind"] == "unverified"

    async def test_a_round_with_nobody_to_offer_to_still_says_who_wrote_it(self) -> None:
        rig = Rig(["U0AUTHOR"], {"U0AUTHOR": member(team_id="T0OTHER")})
        await rig.offers.send(rig.request())
        assert rig.finished()["author_kind"] == "outside"
        assert rig.finished()["sent"] == 0

    async def test_the_events_never_carry_the_author_or_their_email(self) -> None:
        rig = Rig(
            ["U0AUTHOR", "U1"],
            {"U0AUTHOR": member(email_domain="authors-domain.example"), "U1": member()},
        )
        await rig.offers.send(rig.request())
        text = repr(rig.telemetry.events)
        assert "U0AUTHOR" not in text
        assert "authors-domain" not in text
