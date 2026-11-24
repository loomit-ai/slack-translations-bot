"""Slack interaction payloads as Slack sends them. Made-up identifiers and text only."""

from typing import Any

from slack_translator.domain.action_values import ActionValue, encode

CHANNEL = "C0123ABCDE"
TS = "1795478400.000100"
THREAD_ROOT = "1795478000.000050"
MEMBER = "U0MEMBER"
HOME = "T0HOME"


def shortcut_payload(
    *,
    text: str = "파란 상자는 어디에 있나요?",
    channel: str = CHANNEL,
    ts: str = TS,
    thread_ts: str | None = None,
    user: str = MEMBER,
    user_team: str | None = HOME,
    response_url: str = "https://hooks.slack.test/app/response",
) -> dict[str, Any]:
    message: dict[str, Any] = {"type": "message", "user": "U0CUSTOMER", "ts": ts, "text": text}
    if thread_ts is not None:
        message["thread_ts"] = thread_ts
    user_data: dict[str, Any] = {"id": user, "name": "ana"}
    if user_team is not None:
        user_data["team_id"] = user_team
    return {
        "type": "message_action",
        "callback_id": "translate_message",
        "trigger_id": "13345224609.738474920.8088930838d88f008e0",
        "response_url": response_url,
        "team": {"id": HOME, "domain": "x3m"},
        "user": user_data,
        "channel": {"id": channel, "name": "support"},
        "message": message,
    }


def retry_payload(
    *,
    value: str | None = None,
    channel: str = CHANNEL,
    ts: str = TS,
    thread_root_ts: str | None = None,
    user: str = MEMBER,
    user_team: str | None = HOME,
    response_url: str = "https://hooks.slack.test/actions/response",
) -> dict[str, Any]:
    if value is None:
        value = encode(ActionValue(channel=channel, ts=ts, thread_root_ts=thread_root_ts))
    user_data: dict[str, Any] = {"id": user}
    if user_team is not None:
        user_data["team_id"] = user_team
    return {
        "type": "block_actions",
        "team": {"id": HOME, "domain": "x3m"},
        "user": user_data,
        "channel": {"id": channel},
        "container": {"type": "message", "channel_id": channel, "is_ephemeral": True},
        "response_url": response_url,
        "actions": [
            {"action_id": "translate_retry", "type": "button", "value": value, "block_id": "b1"}
        ],
    }
