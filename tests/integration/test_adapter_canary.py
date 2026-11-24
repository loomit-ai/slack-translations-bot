"""FF-03 for the model adapters: no message text in any log line or counter, on every path.

The source text, the translation and the text echoed in a provider error each carry a unique
marker. The real translator, retry wrapper, Lingua classifier and telemetry run against the fake
endpoint through success, timeout, retry and failure; then every log line and the whole metrics
exposition are searched for each marker, raw and JSON-escaped (TC-056).
"""

import json
import secrets

import pytest
from prometheus_client import generate_latest
from pydantic import SecretStr
from tests.support.fake_translator import (
    Answer,
    Fail,
    FakeTranslatorServer,
    Hang,
    Malformed,
)

from slack_translator.domain.errors import ModelError
from slack_translator.domain.languages import (
    LanguageCode,
    candidates_of,
    parse_language_seed,
)
from slack_translator.infrastructure.call_context import correlation_scope
from slack_translator.infrastructure.lingua_classifier import LinguaClassifier
from slack_translator.infrastructure.openai_translator import OpenAITranslator
from slack_translator.infrastructure.retrying_translator import RetryingTranslator
from slack_translator.infrastructure.system_clock import SystemClock
from slack_translator.infrastructure.telemetry import LogTelemetry

SEED = candidates_of(parse_language_seed("en:team,ko:customer"))
SAFE_ID = "ev-collection-control"


def marker(kind: str) -> str:
    return f"{kind}-{secrets.token_hex(8)}"


async def test_no_marker_reaches_a_log_line_or_a_counter() -> None:
    source, translation, echoed, key = (
        marker("SOURCE"),
        marker("TRANSLATION"),
        marker("ECHOED"),
        marker("KEY"),
    )
    text = f"{source} 파란 상자에 종이 별이 세 개 있어요."
    lines: list[str] = []
    telemetry = LogTelemetry(app_env="development", version="0.1.0", sink=lines.append)
    clock = SystemClock()

    async with FakeTranslatorServer() as fake:
        adapter = OpenAITranslator(
            base_url=fake.base_url,
            model="test-model",
            api_key=SecretStr(key),
            timeout=0.3,
            telemetry=telemetry,
            clock=clock,
        )
        translator = RetryingTranslator(adapter, clock=clock, uniform=lambda low, high: 0.0)
        classifier = LinguaClassifier(telemetry=telemetry, clock=clock, timeout=5.0)
        try:
            with correlation_scope(SAFE_ID):
                await classifier.detect(text, SEED)

                fake.enqueue(Answer(translation))  # success
                assert await translator.translate(text, "en", LanguageCode("ko")) == translation

                fake.enqueue(Hang(), Answer(translation))  # timeout, then success
                assert await translator.translate(text, "en") == translation

                fake.enqueue(Fail(503, echo_input=True), Answer(translation))  # retry
                assert await translator.translate(text, "en") == translation

                fake.enqueue(Fail(429, retry_after=1, echo_input=True), Fail(500, echo_input=True))
                with pytest.raises(ModelError):  # two failures
                    await translator.translate(text, "en")

                fake.enqueue(Fail(401, echo_input=True))  # not retried
                with pytest.raises(ModelError):
                    await translator.translate(text, "en")

                fake.enqueue(Malformed("truncated"), Malformed("empty_content"))
                with pytest.raises(ModelError):
                    await translator.translate(text, "en")

                fake.set_default(Fail(500, echo_input=True))
                with pytest.raises(ModelError):
                    await translator.translate(f"{echoed} {text}", "en")
        finally:
            await translator.aclose()
            await classifier.aclose()

    exposition = generate_latest(telemetry.registry).decode()
    everything = "\n".join([*lines, exposition])
    for secret in (source, translation, echoed, key, text):
        assert secret not in everything
        assert json.dumps(secret)[1:-1] not in everything
    assert SAFE_ID in everything, "the collection control must be visible"
    assert len(lines) >= 12
    assert 'outcome="timeout"' in exposition
