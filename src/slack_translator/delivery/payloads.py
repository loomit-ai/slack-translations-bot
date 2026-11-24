"""Reading Slack interaction payloads into requests.

Payloads are untrusted input (REQ-09): a field that is missing or has the wrong shape means the
whole payload is rejected with `None`. Nothing is guessed.
"""

import re
from typing import Any, Literal, cast

from slack_translator.actions.translate_message import TranslateRequest
from slack_translator.domain.action_values import ActionValueError, parse
from slack_translator.domain.messages import MessageRef, ResponseUrl

_TS = re.compile(r"^\d{10}\.\d{6}$")
_CHANNEL = re.compile(r"^[A-Z][A-Z0-9]{8,}$")


def parse_shortcut(body: dict[str, Any], correlation_id: str) -> TranslateRequest | None:
    """The `translate_message` message shortcut (a `message_action` payload)."""
    user = _mapping(body.get("user"))
    channel = _mapping(body.get("channel"))
    message = _mapping(body.get("message"))
    user_id = _string(user.get("id"))
    channel_id = _string(channel.get("id"), _CHANNEL)
    ts = _string(message.get("ts"), _TS)
    response_url = _string(body.get("response_url"))
    if user_id is None or channel_id is None or ts is None or response_url is None:
        return None

    thread_ts = _string(message.get("thread_ts"), _TS)
    text = message.get("text")
    return TranslateRequest(
        entry="shortcut",
        correlation_id=correlation_id,
        channel=channel_id,
        user_id=user_id,
        team_id=_string(user.get("team_id")),
        ref=MessageRef(
            channel=channel_id,
            ts=ts,
            thread_root_ts=thread_ts if thread_ts not in (None, ts) else None,
        ),
        response_url=ResponseUrl(response_url),
        text=text if isinstance(text, str) else "",
    )


def parse_retry_click(body: dict[str, Any], correlation_id: str) -> TranslateRequest | None:
    """The `translate_retry` button (a `block_actions` payload)."""
    return _parse_click(body, "retry", correlation_id)


def parse_offer_click(body: dict[str, Any], correlation_id: str) -> TranslateRequest | None:
    """The `translate_offer` button. Its value always names the offered language."""
    return _parse_click(body, "offer", correlation_id)


def _parse_click(
    body: dict[str, Any], entry: Literal["retry", "offer"], correlation_id: str
) -> TranslateRequest | None:
    actions = body.get("actions")
    if not isinstance(actions, list) or not actions:
        return None
    raw_value = _mapping(cast(list[Any], actions)[0]).get("value")
    if not isinstance(raw_value, str):
        return None
    try:
        value = parse(raw_value)
    except ActionValueError:
        return None
    if entry == "offer" and value.language is None:
        return None

    user = _mapping(body.get("user"))
    user_id = _string(user.get("id"))
    channel_id = _string(_mapping(body.get("channel")).get("id"), _CHANNEL) or _string(
        _mapping(body.get("container")).get("channel_id"), _CHANNEL
    )
    response_url = _string(body.get("response_url"))
    if user_id is None or channel_id is None or channel_id != value.channel:
        return None

    return TranslateRequest(
        entry=entry,
        correlation_id=correlation_id,
        channel=channel_id,
        user_id=user_id,
        team_id=_string(user.get("team_id")),
        ref=MessageRef(channel=value.channel, ts=value.ts, thread_root_ts=value.thread_root_ts),
        response_url=ResponseUrl(response_url) if response_url is not None else None,
        language=value.language if entry == "offer" else None,
        script=value.script if entry == "offer" else None,
    )


def _mapping(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _string(value: object, pattern: re.Pattern[str] | None = None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if pattern is not None and not pattern.match(value):
        return None
    return value
