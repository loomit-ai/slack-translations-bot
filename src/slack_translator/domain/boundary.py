"""REQ-04 and REQ-05 · Who may use the bot, and where."""

from collections.abc import Collection

from slack_translator.domain.messages import UserFacts


def channel_is_allowed(channel: str, allowlist: Collection[str]) -> bool:
    return channel in allowlist


def actor_is_internal(facts: UserFacts | None, home_team_id: str) -> bool:
    """True for an active, human member of the home workspace.

    A user whose workspace cannot be established is refused. Guests of the home workspace count
    as internal until decision 13 is made; the rule is this one line.
    """
    if facts is None or facts.team_id is None:
        return False
    if facts.is_bot or facts.deleted:
        return False
    return facts.team_id == home_team_id
