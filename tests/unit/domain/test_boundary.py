"""REQ-04 (allowlist) and REQ-05 (home workspace). Guests (decision 13) change in one place."""

from slack_translator.domain.boundary import actor_is_internal, channel_is_allowed
from slack_translator.domain.messages import UserFacts

HOME = "T0HOME"


def facts(**overrides: object) -> UserFacts:
    values: dict[str, object] = {
        "team_id": HOME,
        "is_bot": False,
        "deleted": False,
        "is_restricted": False,
    }
    values.update(overrides)
    return UserFacts(**values)  # type: ignore[arg-type]


def test_channels_on_the_allowlist_are_allowed_and_others_are_not() -> None:
    allowlist = ("C0123ABCDE", "G0123ABCDE")
    assert channel_is_allowed("C0123ABCDE", allowlist)
    assert channel_is_allowed("G0123ABCDE", allowlist)
    assert not channel_is_allowed("C0OTHERONE", allowlist)
    assert not channel_is_allowed("D0123ABCDE", allowlist)


def test_a_member_of_the_home_workspace_is_internal() -> None:
    assert actor_is_internal(facts(), HOME)


def test_a_member_of_another_workspace_is_not_internal() -> None:
    assert not actor_is_internal(facts(team_id="T0OTHER"), HOME)


def test_an_actor_whose_workspace_cannot_be_established_is_not_internal() -> None:
    assert not actor_is_internal(facts(team_id=None), HOME)
    assert not actor_is_internal(None, HOME)


def test_bots_and_deactivated_accounts_are_not_internal() -> None:
    assert not actor_is_internal(facts(is_bot=True), HOME)
    assert not actor_is_internal(facts(deleted=True), HOME)


def test_home_workspace_guests_count_as_internal_until_decision_13_says_otherwise() -> None:
    assert actor_is_internal(facts(is_restricted=True), HOME)
