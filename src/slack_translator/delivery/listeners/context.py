"""What every listener needs, handed over by `create_app`."""

from dataclasses import dataclass

from slack_translator.actions.translate_message import TranslateMessage
from slack_translator.delivery.background import TaskRegistry
from slack_translator.domain.ports import Clock, Telemetry


@dataclass(frozen=True, slots=True)
class ListenerContext:
    translate_message: TranslateMessage
    registry: TaskRegistry
    telemetry: Telemetry
    clock: Clock
