"""Small fakes for the model adapters: a controllable clock and a recording telemetry."""

from dataclasses import dataclass, field
from typing import cast

from slack_translator.domain.errors import ModelCallRecord
from slack_translator.domain.ports import Telemetry


class SteppingClock:
    """TS-06. `now()` moves forward by `tick` on every call, so latency is exact and repeatable."""

    def __init__(self, start: float = 1000.0, tick: float = 0.0) -> None:
        self._now = start
        self._tick = tick
        self.sleeps: list[float] = []

    def now(self) -> float:
        value = self._now
        self._now += self._tick
        return value

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += seconds


@dataclass
class RecordingTelemetry:
    """Only the part of the Telemetry port that the model adapters call."""

    records: list[ModelCallRecord] = field(default_factory=list)
    correlation_ids: list[str | None] = field(default_factory=list)

    def model_call(self, record: ModelCallRecord, *, correlation_id: str | None = None) -> None:
        self.records.append(record)
        self.correlation_ids.append(correlation_id)

    def as_port(self) -> Telemetry:
        return cast(Telemetry, self)
