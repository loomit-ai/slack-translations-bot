"""The ports (IF-02, IF-03, IF-05 and the test seams of section 12).

Adapters in `infrastructure/` implement them; `actions/` receive them by injection from
`delivery/`. This module is the shared contract between the developers: a change here needs the
Project lead's agreement.
"""

from collections.abc import Sequence
from typing import Literal, Protocol

from slack_translator.domain.detection import Detection
from slack_translator.domain.errors import ModelCallRecord
from slack_translator.domain.languages import LanguageCandidate, LanguageCode
from slack_translator.domain.messages import (
    MessageRef,
    OfferRequest,
    PrivateMessage,
    ReadOutcome,
    ResponseUrl,
    SourceMessage,
    UserFacts,
)

Entry = Literal["shortcut", "offer", "retry"]
AckKind = Literal["shortcut", "action"]
RefusalReason = Literal["workspace", "guest", "email_domain", "unverified", "channel"]
DropReason = Literal["not_allowlisted", "subtype", "bot", "empty", "too_short", "duplicate"]
ClassifyResult = Literal["customer", "team", "other", "low_confidence", "error"]
TranslateOutcome = Literal[
    "shown",
    "already_read_language",
    "message_unavailable",
    "nothing_to_translate",
    "failed",
    "delivery_failed",
]
OfferOutcome = Literal["sent", "failed", "expired"]
SlackCallOutcome = Literal["ok", "rate_limited", "error", "timeout"]


class LanguageClassifier(Protocol):
    """IF-02. Same text and configuration give the same result; no side effects.

    `text` is the cleaned text. A result outside `candidates` is returned as `other`.
    Failure raises `ModelError` with category `timeout` or `invalid_response`. It never guesses.
    """

    async def detect(self, text: str, candidates: Sequence[LanguageCandidate]) -> Detection: ...


class Translator(Protocol):
    """IF-03. `target_language` is a BCP 47 tag such as `en`, `ko` or `zh-Hans`.

    Raises `ModelError` on failure. Retrying is not the adapter's job (see `RetryingTranslator`).
    """

    async def translate(
        self,
        text: str,
        target_language: str,
        source_language_hint: LanguageCode | None = None,
    ) -> str: ...


class PrivateResponder(Protocol):
    """IF-05 and ADR-006: the only way the bot shows anything.

    There is deliberately no method that posts to a channel.
    """

    async def via_response_url(
        self,
        url: ResponseUrl,
        message: PrivateMessage,
        mode: Literal["new", "replace", "delete"],
        thread_ts: str | None = None,
    ) -> None: ...

    async def ephemeral(
        self,
        channel: str,
        user: str,
        message: PrivateMessage,
        thread_ts: str | None = None,
    ) -> None: ...

    async def open_modal(self, trigger_id: str, view: dict[str, object]) -> None: ...


class MessageReader(Protocol):
    """IF-05. Returns exactly the message asked for, or `None` when it cannot be read."""

    async def fetch(self, ref: MessageRef) -> SourceMessage | None: ...


class MemberDirectory(Protocol):
    """IF-05. Lookups are cached by the adapter."""

    async def user_facts(self, user_id: str) -> UserFacts | None: ...

    async def channel_members(self, channel_id: str) -> Sequence[str]: ...


class OfferSender(Protocol):
    """FLOW-02 step 5: one private offer per eligible member of the channel.

    Never raises: every failure is counted and ends in silence for that member. It can take up to
    the 60-second cut-off plus any rate-limit wait, so the caller starts it as background work
    and does not wait for it inside an event handler.
    """

    async def send(self, request: OfferRequest) -> None: ...


class ServedChannels(Protocol):
    """Decision 25 / CR-11: a channel is served when the bot is a member of it.

    `is_served` asks Slack and remembers the answer (yes for 10 minutes, no for 60 seconds); an
    error or a channel Slack will not show means "not served" and is not remembered.
    `note_event_from` records that an event arrived from the channel, which proves the bot is in
    it. `forget` drops a channel the bot has left.
    """

    async def is_served(self, channel_id: str) -> bool: ...

    def note_event_from(self, channel_id: str) -> None: ...

    def forget(self, channel_id: str) -> None: ...


class ReadPresenter(Protocol):
    """Turns an outcome into the private message for it (Design UI-03 to UI-09, UI-18)."""

    def present(self, outcome: ReadOutcome) -> PrivateMessage: ...


class Clock(Protocol):
    """TS-06. Everything that depends on time uses this, so tests can control it."""

    def now(self) -> float:
        """Seconds on a monotonic clock."""
        ...

    async def sleep(self, seconds: float) -> None: ...


class Telemetry(Protocol):
    """ADR-008 and LOG-01. One typed method per log event; none can carry message text.

    An implementation writes the JSON log line and updates the counters (MET-01 to MET-09) in
    the same call. Slack identifiers are plain strings; they are never counter labels.
    """

    def startup_ready(self) -> None: ...

    def startup_channel_not_member(self, *, channel_id: str) -> None: ...

    def shutdown(self) -> None: ...

    def startup_serving(self, *, channel_count: int) -> None: ...

    def channel_changed(
        self, *, change: Literal["joined", "left"], channel_id: str, user_id: str | None
    ) -> None: ...

    def request_refused(
        self,
        *,
        reason: RefusalReason,
        entry: Entry,
        correlation_id: str,
        team_id: str | None,
        channel_id: str,
    ) -> None: ...

    def event_dropped(
        self,
        *,
        reason: DropReason,
        slack_event_id: str | None,
        channel_id: str | None,
    ) -> None: ...

    def classify_completed(
        self,
        *,
        language: str,
        result: ClassifyResult,
        confidence: float | None,
        script: str | None,
        entry: Entry | None,
        correlation_id: str,
        channel_id: str | None,
    ) -> None: ...

    def offer_finished(
        self,
        *,
        correlation_id: str,
        channel_id: str,
        message_ts: str,
        sent: int,
        failed: int,
        expired: int,
    ) -> None: ...

    def translate_requested(
        self,
        *,
        entry: Entry,
        correlation_id: str,
        team_id: str | None,
        channel_id: str,
        user_id: str,
        message_ts: str,
        thread_ts: str | None,
    ) -> None: ...

    def model_call(self, record: ModelCallRecord, *, correlation_id: str | None = None) -> None: ...

    def translate_completed(
        self,
        *,
        entry: Entry,
        outcome: TranslateOutcome,
        correlation_id: str,
        target_language: str | None,
        language: str | None,
    ) -> None: ...

    def slack_call(
        self,
        *,
        method: str,
        outcome: SlackCallOutcome,
        slack_error: str | None = None,
        correlation_id: str | None = None,
    ) -> None: ...

    def ack_observed(self, *, kind: AckKind, seconds: float) -> None: ...

    def internal_error(self, *, exception_type: str, correlation_id: str | None) -> None: ...
