"""FLOW-01 · Read on demand: the message-menu shortcut and the Retry button (0.1).

Everything here runs after the acknowledgement (REQ-01). The member only ever sees private
output (ADR-006); a customer sees nothing on any path, errors included.
"""

import asyncio
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from slack_translator.domain.action_values import ActionValue, encode
from slack_translator.domain.boundary import channel_is_allowed, membership
from slack_translator.domain.call_context import attempt_scope
from slack_translator.domain.detection import (
    AlreadyRead,
    Detection,
    Hinted,
    ReadDecision,
    Unhinted,
    decide_read,
)
from slack_translator.domain.errors import (
    ModelError,
    ModelErrorCategory,
    ResponseUrlUnusable,
    SlackDeliveryError,
)
from slack_translator.domain.languages import (
    ConfiguredLanguage,
    LanguageCode,
    Script,
    candidates_of,
)
from slack_translator.domain.messages import (
    AlreadyInReadLanguage,
    ChannelNotAllowed,
    ErrorNotice,
    MessageRef,
    MessageUnavailable,
    NothingToTranslate,
    ReadOutcome,
    ResponseUrl,
    Translated,
    Translating,
    TranslationFailed,
)
from slack_translator.domain.ports import (
    ClassifyResult,
    Clock,
    LanguageClassifier,
    MemberDirectory,
    MessageReader,
    PrivateResponder,
    ReadPresenter,
    RefusalReason,
    ServedChannels,
    Telemetry,
    TranslateOutcome,
    Translator,
)
from slack_translator.domain.retry import retry_pause
from slack_translator.domain.text import (
    MIN_LETTERS,
    clean_for_detection,
    letter_count,
    neutralize_controls,
)

# Room beyond TRANSLATOR_TIMEOUT before the action stops waiting for one attempt (REQ-10).
ATTEMPT_MARGIN: Final = 3.0

DeliveryRoute = Literal["response_url", "chat.postEphemeral"]

# These requests come from a button on a private message, so their answers replace that message.
REPLACES_ITS_MESSAGE: Final = ("retry", "offer")

# The fallback message waits out a short Retry-After once; anything longer is not worth holding a
# member for.
FALLBACK_RETRY_AFTER_LIMIT: Final = 5.0


class HomeWorkspace:
    """The workspace the bot is installed in. Known after `auth.test` at startup (REQ-05)."""

    def __init__(self) -> None:
        self.team_id: str | None = None
        self.bot_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class TranslationPolicy:
    allowlist: tuple[str, ...]
    languages: tuple[ConfiguredLanguage, ...]
    read_language: LanguageCode
    min_confidence: float
    classifier_timeout: float
    translator_timeout: float
    member_email_domains: frozenset[str]

    @property
    def read_language_name(self) -> str:
        return next(lang.name for lang in self.languages if lang.code == self.read_language)


@dataclass(frozen=True, slots=True)
class TranslateRequest:
    """One member asking to read one message.

    `text` is set on the shortcut. On Retry and on an offer click it is unset and the message is
    read again from Slack. An offer click also carries the language the offer named.
    """

    entry: Literal["shortcut", "retry", "offer"]
    correlation_id: str
    channel: str
    user_id: str
    team_id: str | None
    ref: MessageRef
    response_url: ResponseUrl | None
    text: str | None = None
    language: LanguageCode | None = None
    script: Script | None = None


class TranslateMessage:
    def __init__(
        self,
        policy: TranslationPolicy,
        home: HomeWorkspace,
        classifier: LanguageClassifier,
        translator: Translator,
        responder: PrivateResponder,
        reader: MessageReader,
        directory: MemberDirectory,
        served: ServedChannels,
        presenter: ReadPresenter,
        telemetry: Telemetry,
        clock: Clock,
        uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._policy = policy
        self._home = home
        self._classifier = classifier
        self._translator = translator
        self._responder = responder
        self._reader = reader
        self._directory = directory
        self._served = served
        self._presenter = presenter
        self._telemetry = telemetry
        self._clock = clock
        self._uniform = uniform

    async def run(self, request: TranslateRequest) -> None:
        self._telemetry.trace(
            "received",
            correlation_id=request.correlation_id,
            entry=request.entry,
            channel_id=request.channel,
            message_ts=request.ref.ts,
            thread_ts=request.ref.thread_root_ts,
        )
        if not await self._admit(request):
            return
        self._telemetry.translate_requested(
            entry=request.entry,
            correlation_id=request.correlation_id,
            team_id=request.team_id,
            channel_id=request.channel,
            user_id=request.user_id,
            message_ts=request.ref.ts,
            thread_ts=request.ref.thread_root_ts,
        )
        try:
            await self._run(request)
        except Exception as error:
            self._telemetry.internal_error(
                exception_type=type(error).__name__, correlation_id=request.correlation_id
            )
            await self._finish(request, self._failure(request), "failed", None, None)

    async def _admit(self, request: TranslateRequest) -> bool:
        """The boundary checks (REQ-04, REQ-05). Only a request that passes them is counted.

        Until the actor is confirmed as an X3M member, nothing is shown to them, whatever fails:
        an outsider gets no error and no Retry (decision 19).
        """
        started = self._clock.now()
        try:
            verdict, source = await self._membership(request)
        except Exception as error:
            self._unexpected(request, error)
            self._refuse(request, "unverified")
            return False
        self._telemetry.trace(
            "member_checked",
            correlation_id=request.correlation_id,
            outcome=source,
            latency_ms=self._milliseconds_since(started),
            refusal_reason=None if verdict == "member" else verdict,
        )
        if verdict != "member":
            self._refuse(request, verdict)
            return False

        if not channel_is_allowed(request.channel, self._policy.allowlist):
            self._telemetry.trace(
                "channel_checked", correlation_id=request.correlation_id, outcome="list"
            )
            self._refuse(request, "channel")
            await self._deliver(request, ChannelNotAllowed())
            return False
        started = self._clock.now()
        remembered = self._served.is_remembered(request.channel)
        served = await self._served.is_served(request.channel)
        self._telemetry.trace(
            "channel_checked",
            correlation_id=request.correlation_id,
            outcome="cache" if remembered else "lookup",
            latency_ms=self._milliseconds_since(started),
        )
        if not served:
            self._refuse(request, "channel")
            await self._deliver(request, ChannelNotAllowed())
            return False
        self._telemetry.trace("admitted", correlation_id=request.correlation_id)
        return True

    def _milliseconds_since(self, started: float) -> int:
        return max(0, round((self._clock.now() - started) * 1000))

    async def _run(self, request: TranslateRequest) -> None:
        text = request.text
        if text is None:
            method = (
                "conversations.replies" if request.ref.thread_root_ts else "conversations.history"
            )
            source = await self._reader.fetch(request.ref)
            if source is None:
                self._telemetry.trace(
                    "source_read",
                    correlation_id=request.correlation_id,
                    outcome="missing",
                    slack_method=method,
                )
                await self._finish(request, MessageUnavailable(), "message_unavailable", None, None)
                return
            text = source.text
            self._telemetry.trace(
                "source_read",
                correlation_id=request.correlation_id,
                outcome="fetched",
                input_chars=len(text),
                slack_method=method,
            )
        else:
            self._telemetry.trace(
                "source_read",
                correlation_id=request.correlation_id,
                outcome="payload",
                input_chars=len(text),
            )

        cleaned = clean_for_detection(text)
        if letter_count(cleaned) < MIN_LETTERS:
            await self._finish(request, NothingToTranslate(), "nothing_to_translate", None, None)
            return

        decision: ReadDecision
        if request.entry == "offer":
            decision = self._hint_from_offer(request)
            self._telemetry.trace(
                "detection_decided",
                correlation_id=request.correlation_id,
                outcome="from_offer",
                language=request.language,
                script=request.script,
            )
        else:
            detection = await self._detect(request, cleaned)
            decision = decide_read(
                detection,
                self._policy.languages,
                self._policy.read_language,
                self._policy.min_confidence,
            )
            self._telemetry.trace(
                "detection_decided",
                correlation_id=request.correlation_id,
                outcome=(
                    "already_read"
                    if isinstance(decision, AlreadyRead)
                    else "hinted"
                    if isinstance(decision, Hinted)
                    else "unhinted"
                ),
                language=self._known_language(detection) if detection else None,
                script=detection.script if detection else None,
                confidence=detection.confidence if detection else None,
            )
            if isinstance(decision, AlreadyRead):
                await self._finish(
                    request,
                    AlreadyInReadLanguage(decision.language),
                    "already_read_language",
                    None,
                    str(detection.language) if detection else None,
                )
                return

        hint = decision.code if isinstance(decision, Hinted) else None
        source_name = decision.name if isinstance(decision, Hinted) else None

        if request.entry in REPLACES_ITS_MESSAGE:
            route = await self._deliver(request, Translating(), quiet=True)
            self._telemetry.trace(
                "progress_shown",
                correlation_id=request.correlation_id,
                outcome="shown"
                if route
                else "skipped"
                if request.response_url is None
                else "failed",
                slack_method=route,
            )

        translated = await self._translate(request, text, hint)
        if translated is None:
            await self._finish(request, self._failure(request), "failed", None, None)
            return

        shown = neutralize_controls(translated, source=text)
        self._telemetry.trace(
            "output_checked",
            correlation_id=request.correlation_id,
            outcome="unchanged" if shown == translated else "escaped",
        )
        outcome = Translated(
            text=shown,
            source_language=source_name,
            source_language_code=hint,
        )
        await self._finish(request, outcome, "shown", self._policy.read_language, hint)

    def _hint_from_offer(self, request: TranslateRequest) -> ReadDecision:
        """An offer names the language, so there is no detection (FLOW-03). The click translates
        the current text and the offered language is only a hint: an edit does not change it."""
        language = next(
            (lang for lang in self._policy.languages if lang.code == request.language), None
        )
        if language is None:
            return Unhinted()
        return Hinted(code=language.code, name=language.name, script=request.script)

    async def _membership(
        self, request: TranslateRequest
    ) -> tuple[Literal["member"] | RefusalReason, Literal["payload", "cache", "lookup"]]:
        """Look the person up, always (decision 23). The payload only allows an early "no".

        A click that names another workspace is refused without a lookup. Everything else asks
        the directory, which caches successes for 10 minutes and never caches a failure. An
        exception from the directory is not handled here: `_admit` logs its class and refuses.
        """
        home = self._home.team_id
        if home is None:
            return "unverified", "payload"
        if request.team_id is not None and request.team_id != home:
            return "workspace", "payload"
        source = "cache" if self._directory.is_cached(request.user_id) else "lookup"
        facts = await self._directory.user_facts(request.user_id)
        return membership(facts, home, self._policy.member_email_domains), source

    async def _detect(self, request: TranslateRequest, cleaned: str) -> Detection | None:
        candidates = candidates_of(self._policy.languages)
        try:
            async with asyncio.timeout(self._policy.classifier_timeout):
                detection = await self._classifier.detect(cleaned, candidates)
        except (ModelError, TimeoutError):
            self._classified(request, "other", "error", None, None)
            return None
        self._classified(
            request,
            self._known_language(detection),
            self._classify_result(detection),
            detection.confidence,
            detection.script,
        )
        return detection

    def _known_language(self, detection: Detection) -> str:
        known = {lang.code for lang in self._policy.languages}
        return detection.language if detection.language in known else "other"

    def _classify_result(self, detection: Detection) -> ClassifyResult:
        configured = next(
            (lang for lang in self._policy.languages if lang.code == detection.language), None
        )
        if configured is None:
            return "other"
        if detection.confidence < self._policy.min_confidence:
            return "low_confidence"
        return configured.role

    def _classified(
        self,
        request: TranslateRequest,
        language: str,
        result: ClassifyResult,
        confidence: float | None,
        script: str | None,
    ) -> None:
        self._telemetry.classify_completed(
            language=language,
            result=result,
            confidence=confidence,
            script=script,
            entry=request.entry,
            correlation_id=request.correlation_id,
            channel_id=request.channel,
        )

    async def _translate(
        self, request: TranslateRequest, text: str, hint: LanguageCode | None
    ) -> str | None:
        """The translation, or `None` after the one retry has failed too (CR-05, IF-03).

        The first failure that can improve waits `retry_pause`, then one more attempt is made.
        On the Retry path the error note is sent beside the retry, never in front of it: the
        retry must not wait for Slack. The result waits for the note instead, so the note can
        never replace the result.
        """
        note: asyncio.Future[None] | None = None
        first_failure: str | None = None
        try:
            try:
                return await self._attempt(text, hint)
            except ModelError as first:
                pause = retry_pause(first, self._uniform)
                if pause is None:
                    return None
                first_failure = first.category.value
            if request.entry in REPLACES_ITS_MESSAGE:
                note = asyncio.ensure_future(self._show_retry_note(request))
            await self._clock.sleep(pause)
            self._telemetry.trace(
                "retry_waited",
                correlation_id=request.correlation_id,
                error_category=first_failure,
                latency_ms=round(pause * 1000),
                attempt=2,
            )
            with attempt_scope(2):
                return await self._attempt(text, hint)
        except ModelError:
            return None
        finally:
            if note is not None:
                await asyncio.gather(note, return_exceptions=True)

    async def _show_retry_note(self, request: TranslateRequest) -> None:
        route = await self._deliver(request, ErrorNotice(), quiet=True)
        self._telemetry.trace(
            "retry_note_shown",
            correlation_id=request.correlation_id,
            outcome="shown" if route else "failed",
            slack_method=route,
        )

    async def _attempt(self, text: str, hint: LanguageCode | None) -> str:
        """One call. A call that outlives its deadline or answers nothing is a failed attempt."""
        deadline = self._policy.translator_timeout + ATTEMPT_MARGIN
        try:
            async with asyncio.timeout(deadline):
                translated = await self._translator.translate(
                    text, self._policy.read_language, hint
                )
        except TimeoutError:
            raise ModelError(ModelErrorCategory.TIMEOUT, "translate") from None
        if not translated.strip():
            raise ModelError(ModelErrorCategory.INVALID_RESPONSE, "translate")
        return translated

    def _failure(self, request: TranslateRequest) -> TranslationFailed:
        value = ActionValue(
            channel=request.channel,
            ts=request.ref.ts,
            thread_root_ts=request.ref.thread_root_ts,
        )
        return TranslationFailed(retry_value=encode(value))

    def _refuse(self, request: TranslateRequest, reason: RefusalReason) -> None:
        self._telemetry.request_refused(
            reason=reason,
            entry=request.entry,
            correlation_id=request.correlation_id,
            team_id=request.team_id,
            channel_id=request.channel,
        )

    async def _finish(
        self,
        request: TranslateRequest,
        outcome: ReadOutcome,
        result: TranslateOutcome,
        target_language: str | None,
        language: str | None,
    ) -> None:
        route = await self._deliver(request, outcome)
        self._telemetry.trace(
            "delivered_via",
            correlation_id=request.correlation_id,
            outcome="ok" if route == "response_url" else "fallback_ok" if route else "failed",
            slack_method=route,
        )
        self._telemetry.translate_completed(
            entry=request.entry,
            outcome=result if route else "delivery_failed",
            correlation_id=request.correlation_id,
            target_language=target_language,
            language=language,
        )

    async def _deliver(
        self, request: TranslateRequest, outcome: ReadOutcome, *, quiet: bool = False
    ) -> DeliveryRoute | None:
        """Response URL first; on any trouble, `chat.postEphemeral` to the same member (UI-17).

        Returns the way the member was shown the message, or `None`. When both ways fail nothing
        more is sent: the failure is already counted by the gateway (MET-07), and a second message
        would only hit the same broken transport. `quiet` is for the progress notes: if one cannot
        be delivered the result still can.
        """
        message = self._presenter.present(outcome)
        mode: Literal["new", "replace"] = (
            "replace" if request.entry in REPLACES_ITS_MESSAGE else "new"
        )
        thread_ts = request.ref.thread_root_ts
        if request.response_url is not None:
            try:
                await self._responder.via_response_url(
                    request.response_url, message, mode, thread_ts
                )
                return "response_url"
            except (ResponseUrlUnusable, SlackDeliveryError):
                pass
            except Exception as error:
                self._unexpected(request, error)
        if quiet:
            return None
        for attempt in (1, 2):
            try:
                await self._responder.ephemeral(
                    request.channel, request.user_id, message, thread_ts
                )
                return "chat.postEphemeral"
            except SlackDeliveryError as error:
                wait = error.retry_after
                if attempt == 1 and wait is not None and wait <= FALLBACK_RETRY_AFTER_LIMIT:
                    await self._clock.sleep(wait)
                    self._telemetry.trace(
                        "rate_limit_waited",
                        correlation_id=request.correlation_id,
                        slack_method="chat.postEphemeral",
                        latency_ms=round(wait * 1000),
                    )
                    continue
                return None
            except Exception as error:
                self._unexpected(request, error)
                return None
        return None

    def _unexpected(self, request: TranslateRequest, error: Exception) -> None:
        self._telemetry.internal_error(
            exception_type=type(error).__name__, correlation_id=request.correlation_id
        )
