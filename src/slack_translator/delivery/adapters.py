"""Where `create_app` gets the classifier, translator and telemetry when nothing is injected.

Tests inject fakes through `Overrides` instead. `AdaptersNotAvailable` is for an adapter that is
not built yet; every adapter exists now.
"""

from slack_translator import __version__
from slack_translator.delivery.settings import Settings
from slack_translator.domain.ports import Clock, LanguageClassifier, Telemetry, Translator
from slack_translator.infrastructure.lingua_classifier import LinguaClassifier
from slack_translator.infrastructure.openai_translator import OpenAITranslator
from slack_translator.infrastructure.telemetry import LogTelemetry


class AdaptersNotAvailable(RuntimeError):
    """A real adapter is not built yet."""


def build_telemetry(settings: Settings) -> Telemetry:
    return LogTelemetry(app_env=settings.app_env, version=__version__, log_level=settings.log_level)


def build_classifier(settings: Settings, telemetry: Telemetry, clock: Clock) -> LanguageClassifier:
    return LinguaClassifier(telemetry=telemetry, clock=clock, timeout=settings.classifier_timeout)


def build_translator(settings: Settings, telemetry: Telemetry, clock: Clock) -> Translator:
    """One attempt per call. The one retry belongs to the action (CR-05)."""
    return OpenAITranslator(
        base_url=settings.translator_base_url,
        model=settings.translator_model,
        api_key=settings.translator_api_key,
        timeout=settings.translator_timeout,
        telemetry=telemetry,
        clock=clock,
        extra_body=settings.translator_extra_body,
    )
