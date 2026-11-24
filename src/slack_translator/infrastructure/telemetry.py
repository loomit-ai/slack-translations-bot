"""ADR-008, LOG-01 to LOG-03 and MET-01 to MET-09 · JSON log lines and Prometheus counters.

One typed method per log event (the `Telemetry` port). Each writes at most one JSON line, built
only from the LOG-01 field list, and updates the counters in the same call. No method takes free
text: values that could hold message text are enumerations, and free-form strings (identifiers,
Slack error codes, exception names, language tags) must match a short pattern or they are left
out of the line. Counters live in an injected registry, so nothing is global.
"""

import json
import logging
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Final, Literal

from prometheus_client import CollectorRegistry, Counter, Histogram

from slack_translator.domain.call_context import current_correlation_id
from slack_translator.domain.errors import ErrorCategory, ModelCallRecord
from slack_translator.domain.languages import SUPPORTED_LANGUAGES
from slack_translator.domain.ports import (
    AckKind,
    AuthorKind,
    ClassifyResult,
    DropReason,
    Entry,
    RefusalReason,
    SlackCallOutcome,
    TranslateOutcome,
)

LogSink = Callable[[str], None]

# LOG-01. Anything outside this list is a bug in this module and raises.
ALLOWED_LOG_FIELDS: Final = frozenset(
    {
        "ts", "level", "event", "app_env", "version", "correlation_id", "slack_event_id",
        "team_id", "channel_id", "user_id", "message_ts", "thread_ts", "entry", "language",
        "script", "confidence", "target_language", "operation", "provider", "model",
        "prompt_version", "attempt", "latency_ms", "input_chars", "prompt_tokens",
        "completion_tokens", "outcome", "error_category", "http_status", "slack_method",
        "slack_error", "drop_reason", "recipient_count", "exception_type", "refusal_reason",
        "author_kind", "logger", "channel_count",
    }
)  # fmt: skip

# LOG-03
THIRD_PARTY_LOGGERS: Final = ("slack_bolt", "slack_sdk", "aiohttp", "httpx", "httpcore")

_LEVELS: Final = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_LANGUAGE_TAG: Final = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_SLACK_ERROR: Final = re.compile(r"[a-z0-9_.:-]{1,80}")
_EXCEPTION_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,99}")
_SCRIPTS: Final = frozenset({"Hans", "Hant"})

_MODEL_BUCKETS: Final = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60)
_ACK_BUCKETS: Final = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5)

_ERROR_CATEGORY_OF_REFUSAL: Final[dict[str, ErrorCategory]] = {
    "workspace": ErrorCategory.REFUSED_WORKSPACE,
    "guest": ErrorCategory.REFUSED_WORKSPACE,
    "email_domain": ErrorCategory.REFUSED_WORKSPACE,
    "unverified": ErrorCategory.REFUSED_WORKSPACE,
    "channel": ErrorCategory.REFUSED_CHANNEL,
}


def _stdout(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _identifier(value: str | None) -> str | None:
    return value if value is not None and _IDENTIFIER.fullmatch(value) else None


def _language(value: str | None) -> str:
    """A language code Lingua knows, or `other`. Keeps log values and label values bounded."""
    return value if value is not None and value in SUPPORTED_LANGUAGES else "other"


def _language_tag(value: str | None) -> str | None:
    return value if value is not None and _LANGUAGE_TAG.fullmatch(value) else None


def _confidence(value: float | None) -> float | None:
    if value is None or not isfinite(value):
        return None
    return round(min(max(value, 0.0), 1.0), 6)


class LogTelemetry:
    """The `Telemetry` port: JSON lines on standard output and Prometheus counters.

    `registry` is what `/metrics` exposes. `sink` takes one finished JSON line (tests pass
    `list.append`). `log_level` is CFG-14: it filters lines, never counters.
    """

    def __init__(
        self,
        *,
        app_env: str,
        version: str,
        log_level: str = "INFO",
        registry: CollectorRegistry | None = None,
        sink: LogSink | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.registry = registry if registry is not None else CollectorRegistry()
        self._app_env = app_env
        self._version = version
        self._threshold = _LEVELS[log_level]
        self._sink = sink if sink is not None else _stdout
        self._now = now

        reg = self.registry
        call_labels = ["operation", "provider", "model", "outcome"]
        self._classified = Counter(
            "translator_messages_classified_total",
            "Messages classified, by language and result",
            ["language", "result"],
            registry=reg,
        )
        self._dropped = Counter(
            "translator_events_dropped_total", "Message events dropped", ["reason"], registry=reg
        )
        self._requested = Counter(
            "translator_translations_requested_total",
            "Translations requested",
            ["entry"],
            registry=reg,
        )
        self._completed = Counter(
            "translator_translations_completed_total",
            "Translations that ended, by outcome",
            ["entry", "outcome"],
            registry=reg,
        )
        self._model_calls = Counter(
            "translator_model_calls_total", "Model calls", call_labels, registry=reg
        )
        self._model_seconds = Histogram(
            "translator_model_call_seconds",
            "Model call latency",
            call_labels,
            buckets=_MODEL_BUCKETS,
            registry=reg,
        )
        self._model_tokens = Counter(
            "translator_model_tokens_total",
            "Tokens reported by the model provider",
            [*call_labels, "kind"],
            registry=reg,
        )
        self._offers = Counter(
            "translator_offers_total",
            "Private offers, by outcome and by who wrote the message",
            ["outcome", "author_kind"],
            registry=reg,
        )
        self._slack_calls = Counter(
            "translator_slack_calls_total",
            "Slack Web API calls",
            ["method", "outcome"],
            registry=reg,
        )
        self._refused = Counter(
            "translator_requests_refused_total",
            "Requests refused at the boundary",
            ["reason"],
            registry=reg,
        )
        self._channel_changes = Counter(
            "translator_channel_changes_total",
            "The bot being added to or removed from a channel",
            ["change"],
            registry=reg,
        )
        self._ack = Histogram(
            "translator_ack_seconds",
            "Time to acknowledge a Slack interaction",
            ["kind"],
            buckets=_ACK_BUCKETS,
            registry=reg,
        )

    def guard_third_party_loggers(self) -> None:
        """LOG-03: hold the Slack, aiohttp and httpx loggers at WARNING and replace their text.

        Their records become `third_party.log` lines with level, the library's logger name (from
        the fixed list above) and exception class only, never the library's own message.
        """
        handler = _ThirdPartyHandler(self)
        for name in THIRD_PARTY_LOGGERS:
            logger = logging.getLogger(name)
            logger.setLevel(logging.WARNING)
            logger.handlers = [handler]
            logger.propagate = False

    # Startup and shutdown

    def startup_ready(self) -> None:
        self._emit("startup.ready", "INFO")

    def startup_channel_not_member(self, *, channel_id: str) -> None:
        self._emit("startup.channel_not_member", "WARNING", channel_id=_identifier(channel_id))

    def shutdown(self) -> None:
        self._emit("shutdown", "INFO")

    def startup_serving(self, *, channel_count: int) -> None:
        self._emit("startup.serving", "INFO", channel_count=max(channel_count, 0))

    def channel_changed(
        self, *, change: Literal["joined", "left"], channel_id: str, user_id: str | None
    ) -> None:
        self._channel_changes.labels(change).inc()
        self._emit(
            f"channel.{change}",
            "INFO",
            channel_id=_identifier(channel_id),
            user_id=_identifier(user_id),
        )

    # Requests and events

    def request_refused(
        self,
        *,
        reason: RefusalReason,
        entry: Entry,
        correlation_id: str,
        team_id: str | None,
        channel_id: str,
    ) -> None:
        self._refused.labels(reason).inc()
        self._emit(
            "request.refused",
            "INFO",
            error_category=_ERROR_CATEGORY_OF_REFUSAL[reason].value,
            refusal_reason=reason,
            entry=entry,
            correlation_id=_identifier(correlation_id),
            team_id=_identifier(team_id),
            channel_id=_identifier(channel_id),
        )

    def event_dropped(
        self, *, reason: DropReason, slack_event_id: str | None, channel_id: str | None
    ) -> None:
        self._dropped.labels(reason).inc()
        self._emit(
            "event.dropped",
            "INFO",
            drop_reason=reason,
            slack_event_id=_identifier(slack_event_id),
            channel_id=_identifier(channel_id),
        )

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
    ) -> None:
        label = _language(language)
        self._classified.labels(label, result).inc()
        self._emit(
            "classify.completed",
            "INFO",
            language=label,
            outcome=result,
            confidence=_confidence(confidence),
            script=script if script in _SCRIPTS else None,
            entry=entry,
            correlation_id=_identifier(correlation_id),
            channel_id=_identifier(channel_id),
        )

    def offer_finished(
        self,
        *,
        correlation_id: str,
        channel_id: str,
        message_ts: str,
        sent: int,
        failed: int,
        expired: int,
        author_kind: AuthorKind,
    ) -> None:
        # LOG-01 has one count field, so each non-empty outcome gets its own line.
        shared = {
            "correlation_id": _identifier(correlation_id),
            "channel_id": _identifier(channel_id),
            "message_ts": _identifier(message_ts),
            "author_kind": author_kind,
        }
        counts = (("sent", sent), ("failed", failed), ("expired", expired))
        for outcome, count in counts:
            if count > 0:
                self._offers.labels(outcome, author_kind).inc(count)
                self._emit(
                    "offer.finished", "INFO", outcome=outcome, recipient_count=count, **shared
                )
        if sent + failed + expired == 0:
            self._emit("offer.finished", "INFO", recipient_count=0, **shared)

    # Translation

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
    ) -> None:
        self._requested.labels(entry).inc()
        self._emit(
            "translate.requested",
            "INFO",
            entry=entry,
            correlation_id=_identifier(correlation_id),
            team_id=_identifier(team_id),
            channel_id=_identifier(channel_id),
            user_id=_identifier(user_id),
            message_ts=_identifier(message_ts),
            thread_ts=_identifier(thread_ts),
        )

    def model_call(self, record: ModelCallRecord, *, correlation_id: str | None = None) -> None:
        labels = (record.operation, record.provider, record.model, record.outcome)
        self._model_calls.labels(*labels).inc()
        self._model_seconds.labels(*labels).observe(max(record.latency_ms, 0) / 1000)
        for kind, tokens in (
            ("prompt", record.prompt_tokens),
            ("completion", record.completion_tokens),
        ):
            if tokens is not None and tokens >= 0:
                self._model_tokens.labels(*labels, kind).inc(tokens)

        failed = record.outcome != "ok"
        self._emit(
            "model.call",
            "WARNING" if failed else "INFO",
            operation=record.operation,
            provider=record.provider,
            model=record.model,
            attempt=record.attempt,
            latency_ms=record.latency_ms,
            input_chars=record.input_chars,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            outcome=record.outcome,
            error_category=record.outcome if failed else None,
            http_status=record.http_status,
            correlation_id=_identifier(correlation_id or current_correlation_id()),
            prompt_version=record.prompt_version,
        )

    def translate_completed(
        self,
        *,
        entry: Entry,
        outcome: TranslateOutcome,
        correlation_id: str,
        target_language: str | None,
        language: str | None,
    ) -> None:
        self._completed.labels(entry, outcome).inc()
        self._emit(
            "translate.completed",
            "INFO",
            entry=entry,
            outcome=outcome,
            correlation_id=_identifier(correlation_id),
            target_language=_language_tag(target_language),
            language=_language(language) if language is not None else None,
        )

    # Slack and failures

    def slack_call(
        self,
        *,
        method: str,
        outcome: SlackCallOutcome,
        slack_error: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self._slack_calls.labels(_identifier(method) or "other", outcome).inc()
        if outcome == "ok":
            return
        self._emit(
            "slack.call_failed",
            "WARNING",
            slack_method=_identifier(method),
            slack_error=slack_error
            if slack_error and _SLACK_ERROR.fullmatch(slack_error)
            else None,
            outcome=outcome,
            correlation_id=_identifier(correlation_id),
        )

    def ack_observed(self, *, kind: AckKind, seconds: float) -> None:
        if isfinite(seconds) and seconds >= 0:
            self._ack.labels(kind).observe(seconds)

    def internal_error(self, *, exception_type: str, correlation_id: str | None) -> None:
        self._emit(
            "internal.error",
            "ERROR",
            exception_type=exception_type if _EXCEPTION_NAME.fullmatch(exception_type) else None,
            correlation_id=_identifier(correlation_id),
        )

    def emit_third_party(self, level: str, logger: str | None, exception_type: str | None) -> None:
        """Called by the LOG-03 guard. Not part of the `Telemetry` port."""
        self._emit(
            "third_party.log",
            level,
            logger=logger if logger in THIRD_PARTY_LOGGERS else None,
            exception_type=(
                exception_type
                if exception_type is not None and _EXCEPTION_NAME.fullmatch(exception_type)
                else None
            ),
        )

    def _emit(self, event: str, level: str, **fields: Any) -> None:
        if _LEVELS[level] < self._threshold:
            return
        line: dict[str, Any] = {
            "ts": self._now()
            .astimezone(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": level,
            "event": event,
            "app_env": self._app_env,
            "version": self._version,
        }
        line.update({key: value for key, value in fields.items() if value is not None})
        if not line.keys() <= ALLOWED_LOG_FIELDS:
            raise ValueError("a log line may only carry the LOG-01 fields")
        self._sink(json.dumps(line, separators=(",", ":"), allow_nan=False))


class _ThirdPartyHandler(logging.Handler):
    """Turns any record from a guarded library into a `third_party.log` line without its text."""

    def __init__(self, telemetry: LogTelemetry) -> None:
        super().__init__(level=logging.WARNING)
        self._telemetry = telemetry

    def emit(self, record: logging.LogRecord) -> None:
        exception = record.exc_info[0] if record.exc_info else None
        self._telemetry.emit_third_party(
            "ERROR" if record.levelno >= logging.ERROR else "WARNING",
            record.name.split(".")[0],
            exception.__name__ if exception is not None else None,
        )
