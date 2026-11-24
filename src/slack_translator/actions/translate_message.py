"""FLOW-01 · Read on demand: the message-menu shortcut and the Retry button (0.1).

Everything here runs after the acknowledgement (REQ-01). The member only ever sees private
output (ADR-006); a customer sees nothing on any path, errors included.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from slack_translator.domain.action_values import ActionValue, encode
from slack_translator.domain.boundary import channel_is_allowed, membership
from slack_translator.domain.detection import (
    AlreadyRead,
    Detection,
    Hinted,
    decide_read,
)
from slack_translator.domain.errors import (
    ModelError,
    ResponseUrlUnusable,
    SlackDeliveryError,
)
from slack_translator.domain.languages import ConfiguredLanguage, LanguageCode, candidates_of
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
    LanguageClassifier,
    MemberDirectory,
    MessageReader,
    PrivateResponder,
    ReadPresenter,
    RefusalReason,
    Telemetry,
    TranslateOutcome,
    Translator,
)
from slack_translator.domain.text import (
    MIN_LETTERS,
    clean_for_detection,
    letter_count,
    neutralize_controls,
)


class HomeWorkspace:
    """The workspace the bot is installed in. Known after `auth.test` at startup (REQ-05)."""

    def __init__(self) -> None:
        self.team_id: str | None = None


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
    """One member asking to read one message. `text` is set on the shortcut, unset on Retry."""

    entry: Literal["shortcut", "retry"]
    correlation_id: str
    channel: str
    user_id: str
    team_id: str | None
    ref: MessageRef
    response_url: ResponseUrl | None
    text: str | None = None


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
        presenter: ReadPresenter,
        telemetry: Telemetry,
    ) -> None:
        self._policy = policy
        self._home = home
        self._classifier = classifier
        self._translator = translator
        self._responder = responder
        self._reader = reader
        self._directory = directory
        self._presenter = presenter
        self._telemetry = telemetry
        self._notices: dict[str, list[asyncio.Task[bool]]] = {}

    async def run(self, request: TranslateRequest) -> None:
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
        finally:
            await self._settle_notices(request)

    async def _admit(self, request: TranslateRequest) -> bool:
        """The boundary checks (REQ-04, REQ-05). Only a request that passes them is counted.

        Until the actor is confirmed as an X3M member, nothing is shown to them, whatever fails:
        an outsider gets no error and no Retry (decision 19).
        """
        try:
            verdict = await self._membership(request)
        except Exception as error:
            self._unexpected(request, error)
            self._refuse(request, "unverified")
            return False
        if verdict != "member":
            self._refuse(request, verdict)
            return False
        if not channel_is_allowed(request.channel, self._policy.allowlist):
            self._refuse(request, "channel")
            await self._deliver(request, ChannelNotAllowed())
            return False
        return True

    def retry_notice(self, request: TranslateRequest) -> Callable[[ModelError], Awaitable[None]]:
        """What to show while the translator retries after a failed first attempt.

        Only where the bot can replace its own note, which is the Retry path. On the shortcut path
        no progress note exists, so there is nothing to update and nothing is shown.
        """

        async def notice(_: ModelError) -> None:
            # The note is sent beside the retry, never in front of it: the provider retry must not
            # wait for Slack. `_settle_notices` makes the result wait for the note instead, so
            # the note can never replace the result.
            if request.entry == "retry":
                task = asyncio.ensure_future(self._deliver(request, ErrorNotice(), quiet=True))
                self._notices.setdefault(request.correlation_id, []).append(task)

        return notice

    async def _settle_notices(self, request: TranslateRequest) -> None:
        tasks = self._notices.pop(request.correlation_id, [])
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, request: TranslateRequest) -> None:
        text = request.text
        if text is None:
            source = await self._reader.fetch(request.ref)
            if source is None:
                await self._finish(request, MessageUnavailable(), "message_unavailable", None, None)
                return
            text = source.text

        cleaned = clean_for_detection(text)
        if letter_count(cleaned) < MIN_LETTERS:
            await self._finish(request, NothingToTranslate(), "nothing_to_translate", None, None)
            return

        detection = await self._detect(request, cleaned)
        decision = decide_read(
            detection,
            self._policy.languages,
            self._policy.read_language,
            self._policy.min_confidence,
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

        if request.entry == "retry":
            await self._deliver(request, Translating(), quiet=True)

        translated = await self._translate(text, hint)
        if translated is None:
            await self._finish(request, self._failure(request), "failed", None, None)
            return

        outcome = Translated(
            text=neutralize_controls(translated, source=text),
            source_language=source_name,
            source_language_code=hint,
        )
        await self._finish(request, outcome, "shown", self._policy.read_language, hint)

    async def _membership(self, request: TranslateRequest) -> Literal["member"] | RefusalReason:
        """Look the person up, always (decision 23). The payload only allows an early "no".

        A click that names another workspace is refused without a lookup. Everything else asks
        the directory, which caches successes for 10 minutes and never caches a failure. An
        exception from the directory is not handled here: `_admit` logs its class and refuses.
        """
        home = self._home.team_id
        if home is None:
            return "unverified"
        if request.team_id is not None and request.team_id != home:
            return "workspace"
        facts = await self._directory.user_facts(request.user_id)
        return membership(facts, home, self._policy.member_email_domains)

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

    async def _translate(self, text: str, hint: LanguageCode | None) -> str | None:
        deadline = self._policy.translator_timeout * 2 + 6
        try:
            async with asyncio.timeout(deadline):
                translated = await self._translator.translate(
                    text, self._policy.read_language, hint
                )
        except (ModelError, TimeoutError):
            return None
        return translated if translated.strip() else None

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
        await self._settle_notices(request)
        delivered = await self._deliver(request, outcome)
        self._telemetry.translate_completed(
            entry=request.entry,
            outcome=result if delivered else "delivery_failed",
            correlation_id=request.correlation_id,
            target_language=target_language,
            language=language,
        )

    async def _deliver(
        self, request: TranslateRequest, outcome: ReadOutcome, *, quiet: bool = False
    ) -> bool:
        """Response URL first; on any trouble, `chat.postEphemeral` to the same member (UI-17).

        Returns whether the member was shown the message. When both ways fail nothing more is sent:
        the failure is already counted by the gateway (MET-07), and a second message would only
        hit the same broken transport. `quiet` is for the progress notes: if one cannot be
        delivered the result still can.
        """
        message = self._presenter.present(outcome)
        mode: Literal["new", "replace"] = "replace" if request.entry == "retry" else "new"
        thread_ts = request.ref.thread_root_ts
        if request.response_url is not None:
            try:
                await self._responder.via_response_url(
                    request.response_url, message, mode, thread_ts
                )
                return True
            except (ResponseUrlUnusable, SlackDeliveryError):
                pass
            except Exception as error:
                self._unexpected(request, error)
        if quiet:
            return False
        try:
            await self._responder.ephemeral(request.channel, request.user_id, message, thread_ts)
        except SlackDeliveryError:
            return False
        except Exception as error:
            self._unexpected(request, error)
            return False
        return True

    def _unexpected(self, request: TranslateRequest, error: Exception) -> None:
        self._telemetry.internal_error(
            exception_type=type(error).__name__, correlation_id=request.correlation_id
        )
