"""Where `create_app` gets the classifier, translator and telemetry when nothing is injected.

The telemetry adapter is not built yet: its builder raises `AdaptersNotAvailable` until it lands.
Tests inject fakes through `Overrides` instead.
"""

from slack_translator.delivery.settings import Settings
from slack_translator.domain.ports import Clock, LanguageClassifier, Telemetry, Translator
from slack_translator.infrastructure.lingua_classifier import LinguaClassifier
from slack_translator.infrastructure.openai_translator import OpenAITranslator
from slack_translator.infrastructure.retrying_translator import RetryingTranslator


class AdaptersNotAvailable(RuntimeError):
    """A real adapter is not built yet."""


def build_telemetry(settings: Settings) -> Telemetry:
    raise AdaptersNotAvailable("telemetry")


def build_classifier(settings: Settings, telemetry: Telemetry, clock: Clock) -> LanguageClassifier:
    return LinguaClassifier(telemetry=telemetry, clock=clock, timeout=settings.classifier_timeout)


def build_translator(settings: Settings, telemetry: Telemetry, clock: Clock) -> Translator:
    return RetryingTranslator(
        OpenAITranslator(
            base_url=settings.translator_base_url,
            model=settings.translator_model,
            api_key=settings.translator_api_key,
            timeout=settings.translator_timeout,
            telemetry=telemetry,
            clock=clock,
            extra_body=settings.translator_extra_body,
        ),
        clock=clock,
    )
