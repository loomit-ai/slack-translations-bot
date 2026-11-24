"""Where `create_app` gets the classifier, translator and telemetry when nothing is injected.

Developer 2 builds these adapters (translator, retry, Lingua, telemetry). Until each lands, its
builder here raises `AdaptersNotAvailable`; tests inject fakes through `Overrides` instead.
"""

from slack_translator.delivery.settings import Settings
from slack_translator.domain.ports import Clock, LanguageClassifier, Telemetry, Translator


class AdaptersNotAvailable(RuntimeError):
    """A real adapter is not built yet."""


def build_telemetry(settings: Settings) -> Telemetry:
    raise AdaptersNotAvailable("telemetry")


def build_classifier(settings: Settings, telemetry: Telemetry, clock: Clock) -> LanguageClassifier:
    raise AdaptersNotAvailable("classifier")


def build_translator(settings: Settings, telemetry: Telemetry, clock: Clock) -> Translator:
    raise AdaptersNotAvailable("translator")
