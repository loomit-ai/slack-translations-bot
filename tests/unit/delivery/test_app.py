"""LOG-03 · The third-party log guard is part of the app: without one, startup fails."""

import pytest
from tests.integration.conftest import SETTINGS
from tests.support.fake_ports import (
    FakeClock,
    RecordingTelemetry,
    ScriptedClassifier,
    ScriptedTranslator,
)

from slack_translator.delivery.app import Overrides, create_app
from slack_translator.delivery.settings import Settings
from slack_translator.infrastructure.telemetry import LogTelemetry


def overrides(**more: object) -> Overrides:
    return Overrides(
        classifier=ScriptedClassifier(),
        translator=ScriptedTranslator(),
        clock=FakeClock(),
        **more,  # type: ignore[arg-type]
    )


def test_a_telemetry_adapter_without_a_log_guard_fails_startup_instead_of_skipping_it() -> None:
    settings = Settings.from_environ(SETTINGS)
    with pytest.raises(TypeError, match="log guard"):
        create_app(settings, overrides(telemetry=RecordingTelemetry()))


def test_an_explicit_log_guard_is_accepted_for_a_fake_telemetry() -> None:
    settings = Settings.from_environ(SETTINGS)
    create_app(settings, overrides(telemetry=RecordingTelemetry(), log_guard=lambda: None))


def test_the_real_telemetry_provides_its_own_log_guard() -> None:
    settings = Settings.from_environ(SETTINGS)
    telemetry = LogTelemetry(app_env="development", version="test", sink=lambda line: None)
    create_app(settings, overrides(telemetry=telemetry))
