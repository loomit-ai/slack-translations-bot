"""In-process fakes for the domain ports (TS-03, TS-06, TS-07). Made-up text only."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from slack_translator.domain.detection import Detection
from slack_translator.domain.errors import ModelCallRecord
from slack_translator.domain.languages import LanguageCandidate, LanguageCode


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._now = start
        self.slept: list[float] = []

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self._now += seconds


class ScriptedClassifier:
    """TS-03. Returns a scripted result, raises a scripted error, or waits to be released."""

    def __init__(
        self, result: Detection | Exception | None = None, timeline: list[str] | None = None
    ) -> None:
        self.result: Detection | Exception | None = result or Detection(LanguageCode("ko"), 0.95)
        self.calls: list[str] = []
        self.hold: asyncio.Event | None = None
        self._timeline = timeline

    async def detect(self, text: str, candidates: Sequence[LanguageCandidate]) -> Detection:
        self.calls.append(text)
        if self._timeline is not None:
            self._timeline.append("classifier")
        if self.hold is not None:
            await self.hold.wait()
        if isinstance(self.result, Exception):
            raise self.result
        assert self.result is not None
        return self.result


@dataclass
class TranslateCall:
    text: str
    target_language: str
    source_language_hint: LanguageCode | None


class ScriptedTranslator:
    """Answers from a script, one entry per call; the last entry repeats. Can be held."""

    def __init__(self, *script: str | Exception, timeline: list[str] | None = None) -> None:
        self.script: list[str | Exception] = list(script) or ["translated text"]
        self.calls: list[TranslateCall] = []
        self.hold: asyncio.Event | None = None
        self.on_call: Callable[[], Awaitable[None]] | None = None
        self._timeline = timeline

    async def translate(
        self,
        text: str,
        target_language: str,
        source_language_hint: LanguageCode | None = None,
    ) -> str:
        self.calls.append(TranslateCall(text, target_language, source_language_hint))
        if self._timeline is not None:
            self._timeline.append("translator")
        if self.on_call is not None:
            await self.on_call()
        if self.hold is not None:
            await self.hold.wait()
        index = min(len(self.calls) - 1, len(self.script) - 1)
        step = self.script[index]
        if isinstance(step, Exception):
            raise step
        return step


@dataclass
class RecordedEvent:
    name: str
    fields: dict[str, Any] = field(default_factory=dict)


class RecordingTelemetry:
    """TS-07. Keeps every typed event so tests can read them instead of parsing logs."""

    def __init__(self, timeline: list[str] | None = None) -> None:
        self.events: list[RecordedEvent] = []
        self._timeline = timeline

    def named(self, name: str) -> list[RecordedEvent]:
        return [event for event in self.events if event.name == name]

    def _record(self, name: str, **fields: Any) -> None:
        self.events.append(RecordedEvent(name, fields))
        if self._timeline is not None:
            self._timeline.append(f"telemetry:{name}")

    def startup_ready(self) -> None:
        self._record("startup_ready")

    def startup_channel_not_member(self, **fields: Any) -> None:
        self._record("startup_channel_not_member", **fields)

    def shutdown(self) -> None:
        self._record("shutdown")

    def request_refused(self, **fields: Any) -> None:
        self._record("request_refused", **fields)

    def event_dropped(self, **fields: Any) -> None:
        self._record("event_dropped", **fields)

    def classify_completed(self, **fields: Any) -> None:
        self._record("classify_completed", **fields)

    def offer_finished(self, **fields: Any) -> None:
        self._record("offer_finished", **fields)

    def translate_requested(self, **fields: Any) -> None:
        self._record("translate_requested", **fields)

    def model_call(self, record: ModelCallRecord, *, correlation_id: str | None = None) -> None:
        self._record("model_call", record=record, correlation_id=correlation_id)

    def translate_completed(self, **fields: Any) -> None:
        self._record("translate_completed", **fields)

    def slack_call(self, **fields: Any) -> None:
        self._record("slack_call", **fields)

    def ack_observed(self, **fields: Any) -> None:
        self._record("ack_observed", **fields)

    def internal_error(self, **fields: Any) -> None:
        self._record("internal_error", **fields)


class FakeDirectory:
    """MemberDirectory for the offer step. Lookups can fail or wait, and are counted."""

    def __init__(self, members: list[str], facts: dict[str, Any] | None = None) -> None:
        self.members = members
        self.facts = facts or {}
        self.lookups: list[str] = []
        self.listing_error: Exception | None = None
        self.in_flight = 0
        self.max_in_flight = 0

    async def channel_members(self, channel_id: str) -> list[str]:
        if self.listing_error is not None:
            raise self.listing_error
        return list(self.members)

    async def user_facts(self, user_id: str) -> Any:
        self.lookups.append(user_id)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
            value = self.facts.get(user_id)
            if isinstance(value, Exception):
                raise value
            return value
        finally:
            self.in_flight -= 1


@dataclass
class EphemeralCall:
    channel: str
    user: str
    text: str
    blocks: Any
    thread_ts: str | None


class FakeResponder:
    """PrivateResponder that records `ephemeral` calls. Scripted per user, in order."""

    def __init__(self) -> None:
        self.calls: list[EphemeralCall] = []
        self.script: dict[str, list[Exception]] = {}
        self.in_flight = 0
        self.max_in_flight = 0

    async def ephemeral(
        self, channel: str, user: str, message: Any, thread_ts: str | None = None
    ) -> None:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
            queue = self.script.get(user)
            if queue:
                raise queue.pop(0)
            self.calls.append(EphemeralCall(channel, user, message.text, message.blocks, thread_ts))
        finally:
            self.in_flight -= 1

    async def via_response_url(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("offers are sent with chat.postEphemeral, not a response URL")

    async def open_modal(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("offers never open a modal")
