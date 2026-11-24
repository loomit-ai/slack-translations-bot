"""Fixtures for the adapter tests: a stepping clock, a recording telemetry, the fake endpoint."""

import secrets
from collections.abc import AsyncIterator

import pytest
from tests.support.fake_translator import FakeTranslatorServer
from tests.support.model_fakes import RecordingTelemetry, SteppingClock


@pytest.fixture
def clock() -> SteppingClock:
    return SteppingClock()


@pytest.fixture
def recorder() -> RecordingTelemetry:
    return RecordingTelemetry()


@pytest.fixture
def api_key() -> str:
    return f"key-{secrets.token_hex(8)}"


@pytest.fixture
async def fake() -> AsyncIterator[FakeTranslatorServer]:
    async with FakeTranslatorServer() as server:
        yield server
