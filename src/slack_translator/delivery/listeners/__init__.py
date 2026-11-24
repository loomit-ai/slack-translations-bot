"""Every Slack listener, registered in one place so a test can check each one acknowledges first."""

from slack_bolt.async_app import AsyncApp

from slack_translator.delivery.listeners import (
    channel_membership,
    message_event,
    retry_click,
    shortcut,
)
from slack_translator.delivery.listeners.context import ListenerContext

LISTENER_NAMES = (
    shortcut.NAME,
    retry_click.NAME,
    channel_membership.JOINED,
    channel_membership.LEFT,
    message_event.NAME,
)


def register_all(app: AsyncApp, context: ListenerContext) -> tuple[str, ...]:
    shortcut.register(app, context)
    retry_click.register(app, context)
    channel_membership.register(app, context)
    message_event.register(app, context)
    return LISTENER_NAMES
