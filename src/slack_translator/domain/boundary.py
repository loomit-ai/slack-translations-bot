"""REQ-04 and CR-09 · Who may use the bot, and where."""

from collections.abc import Collection
from typing import Literal

from slack_translator.domain.messages import UserFacts
from slack_translator.domain.ports import RefusalReason


def channel_is_allowed(channel: str, allowlist: Collection[str]) -> bool:
    return channel in allowlist


def membership(facts: UserFacts | None, home_team_id: str) -> Literal["member"] | RefusalReason:
    """Is this person an X3M member, and if not, why not (decision 23, CR-09 part A).

    Fails closed: facts that could not be established are `unverified`. The reason is only for
    counting; the person is shown nothing either way. Guests and email domains join in parts B
    and C, and each refusal reason is declared in `RefusalReason` already.
    """
    if facts is None or not facts.team_id or not facts.team_id.strip():
        return "unverified"
    if facts.team_id != home_team_id or facts.is_stranger:
        return "workspace"
    if facts.is_bot or facts.deleted:
        return "workspace"
    return "member"
