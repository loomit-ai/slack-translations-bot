"""What every listener needs, handed over by `create_app`."""

from dataclasses import dataclass

from slack_translator.actions.classify_message import ClassifyMessage
from slack_translator.actions.translate_message import HomeWorkspace, TranslateMessage
from slack_translator.delivery.background import TaskRegistry
from slack_translator.domain.ports import Clock, OfferSender, ServedChannels, Telemetry


@dataclass(frozen=True, slots=True)
class ListenerContext:
    translate_message: TranslateMessage
    classify_message: ClassifyMessage
    offers: OfferSender
    served: ServedChannels
    home: HomeWorkspace
    registry: TaskRegistry
    telemetry: Telemetry
    clock: Clock
