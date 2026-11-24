"""REQ-04 (allowlist) and CR-09 (membership, decision 23): who may use the bot, and where."""

import pytest

from slack_translator.domain.boundary import channel_is_allowed, membership
from slack_translator.domain.messages import UserFacts

HOME = "T0HOME"
DOMAINS = frozenset({"x3mads.com", "loomit.ai"})


def facts(**overrides: object) -> UserFacts:
    values: dict[str, object] = {
        "team_id": HOME,
        "is_bot": False,
        "deleted": False,
        "is_stranger": False,
        "email_domain": "x3mads.com",
    }
    values.update(overrides)
    return UserFacts(**values)  # type: ignore[arg-type]


def test_channels_on_the_allowlist_are_allowed_and_others_are_not() -> None:
    allowlist = ("C0123ABCDE", "G0123ABCDE")
    assert channel_is_allowed("C0123ABCDE", allowlist)
    assert channel_is_allowed("G0123ABCDE", allowlist)
    assert not channel_is_allowed("C0OTHERONE", allowlist)
    assert not channel_is_allowed("D0123ABCDE", allowlist)


@pytest.mark.parametrize("domain", ["x3mads.com", "loomit.ai", "X3MADS.COM"])
def test_an_active_human_of_the_home_workspace_with_an_allowed_domain_is_a_member(
    domain: str,
) -> None:
    assert membership(facts(email_domain=domain), HOME, DOMAINS) == "member"


@pytest.mark.parametrize(
    ("actor", "reason"),
    [
        (None, "unverified"),
        (facts(team_id=None), "unverified"),
        (facts(team_id=""), "unverified"),
        (facts(team_id="  "), "unverified"),
        (facts(team_id="T0OTHER"), "workspace"),
        (facts(is_stranger=True), "workspace"),
        (facts(team_id="T0OTHER", is_stranger=True), "workspace"),
        (facts(is_bot=True), "workspace"),
        (facts(deleted=True), "workspace"),
        (facts(email_domain=None), "unverified"),
        (facts(email_domain=""), "unverified"),
        (facts(email_domain="gmail.com"), "email_domain"),
        (facts(email_domain="x3mads.com.example.org"), "email_domain"),
        (facts(email_domain="notx3mads.com"), "email_domain"),
        (facts(email_domain="mail.x3mads.com"), "email_domain"),
        (facts(email_domain="x3mads.co"), "email_domain"),
    ],
)
def test_everyone_else_is_refused_with_a_reason(actor: UserFacts | None, reason: str) -> None:
    assert membership(actor, HOME, DOMAINS) == reason


def test_a_missing_workspace_is_checked_before_anything_else() -> None:
    assert membership(facts(team_id=None, is_bot=True, is_stranger=True), HOME, DOMAINS) == (
        "unverified"
    )


def test_a_bot_or_deactivated_account_is_refused_whatever_its_email() -> None:
    assert membership(facts(is_bot=True, email_domain=None), HOME, DOMAINS) == "workspace"
    assert membership(facts(deleted=True, email_domain="gmail.com"), HOME, DOMAINS) == "workspace"


def test_the_domains_come_from_the_caller_not_from_code() -> None:
    assert membership(
        facts(email_domain="partner.example"), HOME, frozenset({"partner.example"})
    ) == ("member")
    assert membership(facts(), HOME, frozenset({"partner.example"})) == "email_domain"
