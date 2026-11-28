"""Slack `message` events as Socket Mode delivers them. Made-up identifiers and text only."""

from typing import Any

from tests.support.payloads import CHANNEL, HOME, TS


def message_event(
    *,
    text: str | None = "파란 상자에 종이 별이 세 개 있어요.",
    channel: str = CHANNEL,
    user: str | None = "U0CUSTOMER",
    ts: str = TS,
    thread_ts: str | None = None,
    subtype: str | None = None,
    bot_id: str | None = None,
    channel_type: str | None = "channel",
    event_id: str = "Ev0123ABCDE",
) -> dict[str, Any]:
    event: dict[str, Any] = {"type": "message", "channel": channel, "ts": ts, "event_ts": ts}
    for key, value in (
        ("text", text),
        ("user", user),
        ("thread_ts", thread_ts),
        ("subtype", subtype),
        ("bot_id", bot_id),
        ("channel_type", channel_type),
    ):
        if value is not None:
            event[key] = value
    return {
        "token": "unused",
        "team_id": HOME,
        "api_app_id": "A0APP",
        "type": "event_callback",
        "event_id": event_id,
        "event_time": 1795478400,
        "event": event,
    }
