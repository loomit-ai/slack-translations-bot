"""REQ-04 and CR-09 · Who may use the bot, and where."""

from collections.abc import Collection
from typing import Literal

from slack_translator.domain.messages import UserFacts
from slack_translator.domain.ports import RefusalReason


def channel_is_allowed(channel: str, allowlist: Collection[str]) -> bool:
    return channel in allowlist


def membership(
    facts: UserFacts | None, home_team_id: str, allowed_domains: Collection[str]
) -> Literal["member"] | RefusalReason:
    """Is this person an X3M member, and if not, why not (decision 23, CR-09).

    A member is an active person in the home workspace, not a Slack Connect user, whose Slack
    email domain is one of `allowed_domains`. Guests are decided by that email alone. The whole
    domain must match, so `notx3mads.com` and `x3mads.com.example.org` are outside.

    Fails closed: anything that could not be established is `unverified`. The reason is only for
    counting; the person is shown nothing either way.
    """
    if facts is None or not facts.team_id or not facts.team_id.strip():
        return "unverified"
    if facts.team_id != home_team_id or facts.is_stranger:
        return "workspace"
    if facts.is_bot or facts.deleted:
        return "workspace"
    if not facts.email_domain:
        return "unverified"
    if facts.email_domain.lower() not in allowed_domains:
        return "email_domain"
    return "member"
