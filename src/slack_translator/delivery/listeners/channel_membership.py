"""CR-11 · The bot being added to or removed from a channel (member_joined_channel and left).

Only the bot's own joins and leaves count; every other member's are ignored without a trace. They
keep the "served" memory current and leave an audit line with the ID of the person who added the
bot. Correctness does not depend on them: the memory also expires by itself (decision 25).
"""

from typing import Any

from slack_bolt.async_app import AsyncApp

from slack_translator.delivery.listeners.context import ListenerContext

JOINED = "member_joined_channel"
LEFT = "member_left_channel"


def register(app: AsyncApp, context: ListenerContext) -> None:
    @app.event(JOINED)
    async def joined(event: dict[str, Any]) -> None:
        channel = _bots_own(event, context)
        if channel is None:
            return
        context.served.note_event_from(channel)
        inviter = event.get("inviter")
        context.telemetry.channel_changed(
            change="joined",
            channel_id=channel,
            user_id=inviter if isinstance(inviter, str) else None,
        )

    @app.event(LEFT)
    async def left(event: dict[str, Any]) -> None:
        channel = _bots_own(event, context)
        if channel is None:
            return
        context.served.forget(channel)
        context.telemetry.channel_changed(change="left", channel_id=channel, user_id=None)


def _bots_own(event: dict[str, Any], context: ListenerContext) -> str | None:
    bot = context.home.bot_user_id
    channel = event.get("channel")
    if bot is None or event.get("user") != bot or not isinstance(channel, str):
        return None
    return channel
