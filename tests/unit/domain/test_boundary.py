"""REQ-04 (allowlist) and CR-09 part A (membership): who may use the bot, and where."""

import pytest

from slack_translator.domain.boundary import channel_is_allowed, membership
from slack_translator.domain.messages import UserFacts

HOME = "T0HOME"


def facts(**overrides: object) -> UserFacts:
    values: dict[str, object] = {
        "team_id": HOME,
        "is_bot": False,
        "deleted": False,
        "is_restricted": False,
        "is_stranger": False,
    }
    values.update(overrides)
    return UserFacts(**values)  # type: ignore[arg-type]


def test_channels_on_the_allowlist_are_allowed_and_others_are_not() -> None:
    allowlist = ("C0123ABCDE", "G0123ABCDE")
    assert channel_is_allowed("C0123ABCDE", allowlist)
    assert channel_is_allowed("G0123ABCDE", allowlist)
    assert not channel_is_allowed("C0OTHERONE", allowlist)
    assert not channel_is_allowed("D0123ABCDE", allowlist)


def test_an_active_human_of_the_home_workspace_is_a_member() -> None:
    assert membership(facts(), HOME) == "member"


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
    ],
)
def test_everyone_else_is_refused_with_a_reason(actor: UserFacts | None, reason: str) -> None:
    assert membership(actor, HOME) == reason


def test_a_missing_workspace_is_checked_before_anything_else() -> None:
    assert membership(facts(team_id=None, is_bot=True, is_stranger=True), HOME) == "unverified"


def test_guests_still_count_as_members_until_part_b_or_c_ships() -> None:
    assert membership(facts(is_restricted=True), HOME) == "member"
