"""FLOW-02 steps 1 to 5 · Classify every human message and hand a confident customer language on.

Runs after Slack has been acknowledged. It never shows anything to anyone and fails silently:
a message that is filtered out, a duplicate, a failed detection and an unexpected error all end
with a count (MET-01, MET-02) and no output (ERR-06, decision 3). There is no author check; the
author goes along so the offer step can leave them out of the recipients.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from slack_translator.domain.detection import Detection
from slack_translator.domain.errors import ModelError
from slack_translator.domain.intake import (
    Dropped,
    MessageEvent,
    classify_result,
    customer_language,
    known_language,
    screen,
)
from slack_translator.domain.languages import ConfiguredLanguage, candidates_of
from slack_translator.domain.messages import MessageRef, OfferRequest
from slack_translator.domain.ports import (
    Clock,
    DropReason,
    LanguageClassifier,
    OfferSender,
    Telemetry,
)


@dataclass(frozen=True, slots=True)
class IntakePolicy:
    languages: tuple[ConfiguredLanguage, ...]
    min_confidence: float
    classifier_timeout: float


class SeenEvents(Protocol):
    """REQ-03. `first_time` checks and inserts in one step, with no `await` in between."""

    def first_time(self, key: str) -> bool: ...


class ClassifyMessage:
    def __init__(
        self,
        *,
        policy: IntakePolicy,
        classifier: LanguageClassifier,
        telemetry: Telemetry,
        seen: SeenEvents,
        offers: OfferSender,
        clock: Clock,
        is_served: Callable[[str], bool],
    ) -> None:
        self._policy = policy
        self._classifier = classifier
        self._telemetry = telemetry
        self._seen = seen
        self._offers = offers
        self._clock = clock
        self._is_served = is_served

    async def run(self, event: MessageEvent, correlation_id: str) -> None:
        try:
            await self._run(event, correlation_id)
        except Exception as error:
            self._telemetry.internal_error(
                exception_type=type(error).__name__, correlation_id=correlation_id
            )

    async def _run(self, event: MessageEvent, correlation_id: str) -> None:
        received_at = self._clock.now()
        screened = screen(event, self._is_served)
        if isinstance(screened, Dropped):
            self._dropped(event, screened.reason)
            return
        if not self._seen.first_time(event.event_id or f"{event.channel}:{event.ts}"):
            self._dropped(event, "duplicate")
            return

        languages = self._policy.languages
        detection = await self._detect(screened.cleaned_text)
        self._telemetry.classify_completed(
            language=known_language(detection, languages),
            result=classify_result(detection, languages, self._policy.min_confidence),
            confidence=detection.confidence if detection is not None else None,
            script=detection.script if detection is not None else None,
            entry=None,
            correlation_id=correlation_id,
            channel_id=event.channel,
        )

        target = customer_language(detection, languages, self._policy.min_confidence)
        if target is None or detection is None:
            return
        await self._offers.send(
            OfferRequest(
                correlation_id=correlation_id,
                ref=MessageRef(
                    channel=event.channel, ts=event.ts, thread_root_ts=event.thread_root_ts
                ),
                author_id=event.user_id,
                language=target.code,
                script=detection.script,
                received_at=received_at,
            )
        )

    async def _detect(self, cleaned: str) -> Detection | None:
        try:
            async with asyncio.timeout(self._policy.classifier_timeout):
                return await self._classifier.detect(cleaned, candidates_of(self._policy.languages))
        except (ModelError, TimeoutError):
            return None

    def _dropped(self, event: MessageEvent, reason: DropReason) -> None:
        self._telemetry.event_dropped(
            reason=reason, slack_event_id=event.event_id, channel_id=event.channel
        )
