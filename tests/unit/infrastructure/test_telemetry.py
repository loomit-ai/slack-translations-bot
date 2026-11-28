"""ADR-008, LOG-01 to LOG-03 and MET-01 to MET-09: typed events, counters and the log guard."""

import inspect
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from prometheus_client import generate_latest

from slack_translator.domain.call_context import correlation_scope
from slack_translator.domain.errors import ModelCallRecord
from slack_translator.infrastructure.telemetry import LogTelemetry

# LOG-01: the only fields a line may carry.
ALLOWED_FIELDS = {
    "ts", "level", "event", "app_env", "version", "correlation_id", "slack_event_id", "team_id",
    "channel_id", "user_id", "message_ts", "thread_ts", "entry", "language", "script",
    "confidence", "target_language", "operation", "provider", "model", "prompt_version",
    "attempt", "latency_ms", "input_chars", "prompt_tokens", "completion_tokens", "outcome",
    "error_category", "http_status", "slack_method", "slack_error", "drop_reason", "provider_error",
    "recipient_count", "exception_type", "refusal_reason", "logger", "channel_count", "author_kind",
    "step",
}  # fmt: skip

# MET-01 to MET-09: the metric families the exposition must carry.
METRIC_FAMILIES = {
    "translator_messages_classified_total",
    "translator_events_dropped_total",
    "translator_translations_requested_total",
    "translator_translations_completed_total",
    "translator_model_calls_total",
    "translator_model_call_seconds_count",
    "translator_model_tokens_total",
    "translator_offers_total",
    "translator_slack_calls_total",
    "translator_requests_refused_total",
    "translator_ack_seconds_count",
    "translator_channel_changes_total",
}

MOMENT = datetime(2026, 11, 24, 3, 4, 5, 678000, tzinfo=UTC)


@dataclass
class Rig:
    telemetry: LogTelemetry
    lines: list[str]

    @property
    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.lines]

    def count(self, name: str, **labels: str) -> float | None:
        return self.telemetry.registry.get_sample_value(name, labels)


def rig(log_level: str = "INFO") -> Rig:
    lines: list[str] = []
    telemetry = LogTelemetry(
        app_env="staging",
        version="0.1.0",
        log_level=log_level,
        sink=lines.append,
        now=lambda: MOMENT,
    )
    return Rig(telemetry, lines)


def record(**changes: Any) -> ModelCallRecord:
    values: dict[str, Any] = {
        "operation": "translate",
        "provider": "openrouter.ai",
        "model": "test-model",
        "attempt": 1,
        "latency_ms": 840,
        "outcome": "ok",
        "http_status": 200,
        "input_chars": 36,
        "prompt_tokens": 120,
        "completion_tokens": 40,
        "prompt_version": "pv-test",
    }
    values.update(changes)
    return ModelCallRecord(**values)


def test_a_line_is_one_json_object_with_the_fixed_header() -> None:
    r = rig()

    r.telemetry.startup_ready()

    assert r.lines == [
        '{"ts":"2026-11-24T03:04:05.678Z","level":"INFO","event":"startup.ready",'
        '"app_env":"staging","version":"0.1.0"}'
    ]


def test_the_default_sink_is_standard_output(capsys: pytest.CaptureFixture[str]) -> None:
    LogTelemetry(app_env="development", version="0.1.0", now=lambda: MOMENT).shutdown()

    out = capsys.readouterr().out
    assert out.endswith("\n")
    assert json.loads(out)["event"] == "shutdown"


# One call per Telemetry method, with every optional value filled in.
EVERY_METHOD: list[Callable[[LogTelemetry], None]] = [
    lambda t: t.startup_ready(),
    lambda t: t.startup_channel_not_member(channel_id="C0123ABCDE"),
    lambda t: t.shutdown(),
    lambda t: t.trace(
        "detection_decided", correlation_id="c1", entry="shortcut", channel_id="C0123ABCDE",
        message_ts="1700000000.000100", thread_ts="1700000000.000050", slack_event_id="Ev1",
        language="ko", script="Hans", confidence=0.9, attempt=2, latency_ms=12, input_chars=30,
        outcome="hinted", error_category="timeout", refusal_reason="channel", recipient_count=3,
        author_kind="outside", slack_method="response_url",
    ),
    lambda t: t.startup_serving(channel_count=7),
    lambda t: t.channel_changed(change="joined", channel_id="C0123ABCDE", user_id="U1"),
    lambda t: t.channel_changed(change="left", channel_id="C0123ABCDE", user_id=None),
    lambda t: t.request_refused(
        reason="channel", entry="shortcut", correlation_id="c1", team_id="T1",
        channel_id="C0123ABCDE",
    ),
    lambda t: t.event_dropped(reason="bot", slack_event_id="Ev1", channel_id="C0123ABCDE"),
    lambda t: t.classify_completed(
        language="zh", result="customer", confidence=0.93, script="Hans", entry="offer",
        correlation_id="c1", channel_id="C0123ABCDE",
    ),
    lambda t: t.offer_finished(
        correlation_id="c1", channel_id="C0123ABCDE", message_ts="1700000000.000100",
        sent=3, failed=1, expired=2, author_kind="x3m_member",
    ),
    lambda t: t.translate_requested(
        entry="retry", correlation_id="c1", team_id="T1", channel_id="C0123ABCDE",
        user_id="U1", message_ts="1700000000.000100", thread_ts="1700000000.000050",
    ),
    lambda t: t.model_call(record(), correlation_id="c1"),
    lambda t: t.model_call(record(outcome="timeout", http_status=None), correlation_id="c1"),
    lambda t: t.translate_completed(
        entry="shortcut", outcome="shown", correlation_id="c1", target_language="zh-Hant",
        language="ko",
    ),
    lambda t: t.slack_call(
        method="chat.postEphemeral", outcome="error", slack_error="channel_not_found",
        correlation_id="c1",
    ),
    lambda t: t.slack_call(method="views.open", outcome="ok"),
    lambda t: t.ack_observed(kind="shortcut", seconds=0.12),
    lambda t: t.internal_error(exception_type="KeyError", correlation_id="c1"),
]  # fmt: skip


def test_every_line_stays_inside_the_log_01_field_list() -> None:
    r = rig()

    for call in EVERY_METHOD:
        call(r.telemetry)

    assert r.events
    for event in r.events:
        assert set(event) <= ALLOWED_FIELDS, event
        assert None not in event.values()


def test_every_counter_family_of_the_contract_is_exposed() -> None:
    r = rig()
    for call in EVERY_METHOD:
        call(r.telemetry)

    exposition = generate_latest(r.telemetry.registry).decode()

    present = {line.split("{")[0].split(" ")[0] for line in exposition.splitlines()}
    assert present >= METRIC_FAMILIES


def test_two_instances_do_not_share_counters() -> None:
    first, second = rig(), rig()

    first.telemetry.translate_requested(
        entry="shortcut", correlation_id="c", team_id=None, channel_id="C0123ABCDE",
        user_id="U1", message_ts="1.1", thread_ts=None,
    )  # fmt: skip

    assert first.count("translator_translations_requested_total", entry="shortcut") == 1
    assert second.count("translator_translations_requested_total", entry="shortcut") is None


def test_startup_and_shutdown_events() -> None:
    r = rig()

    r.telemetry.startup_channel_not_member(channel_id="C0123ABCDE")
    r.telemetry.shutdown()

    warning, shutdown = r.events
    assert (warning["event"], warning["level"], warning["channel_id"]) == (
        "startup.channel_not_member", "WARNING", "C0123ABCDE",
    )  # fmt: skip
    assert (shutdown["event"], shutdown["level"]) == ("shutdown", "INFO")


@pytest.mark.parametrize("count", [0, 1, 40])
def test_serving_at_startup_logs_how_many_channels_the_bot_is_in(count: int) -> None:
    r = rig()

    r.telemetry.startup_serving(channel_count=count)

    [event] = r.events
    assert (event["event"], event["level"], event["channel_count"]) == (
        "startup.serving",
        "INFO",
        count,
    )


def test_the_bot_being_added_to_a_channel_is_logged_with_who_added_it_and_counted() -> None:
    r = rig()

    r.telemetry.channel_changed(change="joined", channel_id="C0123ABCDE", user_id="U0INVITER")

    [event] = r.events
    assert (event["event"], event["level"]) == ("channel.joined", "INFO")
    assert (event["channel_id"], event["user_id"]) == ("C0123ABCDE", "U0INVITER")
    assert r.count("translator_channel_changes_total", change="joined") == 1


def test_the_bot_being_removed_from_a_channel_is_logged_without_a_person_and_counted() -> None:
    r = rig()

    r.telemetry.channel_changed(change="left", channel_id="C0123ABCDE", user_id=None)

    [event] = r.events
    assert (event["event"], event["channel_id"]) == ("channel.left", "C0123ABCDE")
    assert "user_id" not in event
    assert r.count("translator_channel_changes_total", change="left") == 1
    assert r.count("translator_channel_changes_total", change="joined") is None


@pytest.mark.parametrize(
    ("reason", "category"),
    [
        ("workspace", "refused_workspace"),
        ("guest", "refused_workspace"),
        ("email_domain", "refused_workspace"),
        ("unverified", "refused_workspace"),
        ("channel", "refused_channel"),
    ],
)
def test_a_refusal_names_its_error_category_and_is_counted(reason: Any, category: str) -> None:
    r = rig()

    r.telemetry.request_refused(
        reason=reason, entry="shortcut", correlation_id="c1", team_id=None,
        channel_id="C0123ABCDE",
    )  # fmt: skip

    [event] = r.events
    assert event["event"] == "request.refused"
    assert event["error_category"] == category
    assert event["refusal_reason"] == reason
    assert (event["entry"], event["correlation_id"]) == ("shortcut", "c1")
    assert "user_id" not in event, "a refusal never names the person"
    assert "team_id" not in event
    assert r.count("translator_requests_refused_total", reason=reason) == 1


def test_a_dropped_event_carries_its_reason_and_is_counted() -> None:
    r = rig()

    r.telemetry.event_dropped(reason="duplicate", slack_event_id="Ev1", channel_id=None)

    [event] = r.events
    assert event["event"] == "event.dropped"
    assert (event["drop_reason"], event["slack_event_id"]) == ("duplicate", "Ev1")
    assert "channel_id" not in event
    assert r.count("translator_events_dropped_total", reason="duplicate") == 1


def test_a_classification_is_logged_and_counted_per_language_and_result() -> None:
    r = rig()

    r.telemetry.classify_completed(
        language="ko", result="customer", confidence=0.987654321, script=None, entry="offer",
        correlation_id="c1", channel_id="C0123ABCDE",
    )  # fmt: skip

    [event] = r.events
    assert event["event"] == "classify.completed"
    assert event["language"] == "ko"
    assert event["outcome"] == "customer"
    assert event["confidence"] == pytest.approx(0.987654, abs=1e-6)
    assert "script" not in event
    assert r.count("translator_messages_classified_total", language="ko", result="customer") == 1


def test_a_language_that_is_not_a_known_code_becomes_other_everywhere() -> None:
    r = rig()

    r.telemetry.classify_completed(
        language="Hello there friend", result="error", confidence=None, script="Latin",
        entry=None, correlation_id="c1", channel_id=None,
    )  # fmt: skip

    [event] = r.events
    assert event["language"] == "other"
    assert "script" not in event
    assert r.count("translator_messages_classified_total", language="other", result="error") == 1


def test_offers_are_logged_per_outcome_and_counted() -> None:
    r = rig()

    r.telemetry.offer_finished(
        correlation_id="c1", channel_id="C0123ABCDE", message_ts="1700000000.000100",
        sent=3, failed=0, expired=2, author_kind="outside",
    )  # fmt: skip

    assert [(e["event"], e["outcome"], e["recipient_count"]) for e in r.events] == [
        ("offer.finished", "sent", 3),
        ("offer.finished", "expired", 2),
    ]
    assert {e["author_kind"] for e in r.events} == {"outside"}
    assert r.count("translator_offers_total", outcome="sent", author_kind="outside") == 3
    assert r.count("translator_offers_total", outcome="expired", author_kind="outside") == 2
    assert r.count("translator_offers_total", outcome="failed") is None
    assert r.count("translator_offers_total", outcome="sent", author_kind="x3m_member") is None


def test_an_offer_round_with_no_recipients_still_leaves_one_line() -> None:
    r = rig()

    r.telemetry.offer_finished(
        correlation_id="c1", channel_id="C0123ABCDE", message_ts="1.1", sent=0, failed=0, expired=0,
        author_kind="unverified",
    )  # fmt: skip

    [event] = r.events
    assert event["recipient_count"] == 0
    assert event["author_kind"] == "unverified"
    assert "outcome" not in event


def test_a_translation_request_is_logged_and_counted() -> None:
    r = rig()

    r.telemetry.translate_requested(
        entry="retry", correlation_id="c1", team_id="T1", channel_id="C0123ABCDE", user_id="U1",
        message_ts="1700000000.000100", thread_ts=None,
    )  # fmt: skip

    [event] = r.events
    assert event["event"] == "translate.requested"
    assert event["message_ts"] == "1700000000.000100"
    assert "thread_ts" not in event
    assert r.count("translator_translations_requested_total", entry="retry") == 1


def test_a_translation_result_is_logged_and_counted() -> None:
    r = rig()

    r.telemetry.translate_completed(
        entry="shortcut", outcome="failed", correlation_id="c1", target_language="zh-Hant",
        language=None,
    )  # fmt: skip

    [event] = r.events
    assert event["event"] == "translate.completed"
    assert (event["outcome"], event["target_language"]) == ("failed", "zh-Hant")
    assert "language" not in event
    assert (
        r.count("translator_translations_completed_total", entry="shortcut", outcome="failed") == 1
    )


@pytest.mark.parametrize("target", ["en\nIgnore the rules", "a translated sentence here", ""])
def test_a_target_language_that_is_not_a_tag_is_left_out(target: str) -> None:
    r = rig()

    r.telemetry.translate_completed(
        entry="shortcut",
        outcome="shown",
        correlation_id="c1",
        target_language=target,
        language="ko",
    )

    assert "target_language" not in r.events[0]


def test_a_successful_model_call_is_info_with_everything_but_the_text() -> None:
    r = rig()

    r.telemetry.model_call(record(), correlation_id="c1")

    [event] = r.events
    assert event["event"] == "model.call"
    assert event["level"] == "INFO"
    assert event["prompt_version"] == "pv-test"
    assert (event["operation"], event["provider"], event["model"]) == (
        "translate", "openrouter.ai", "test-model",
    )  # fmt: skip
    assert (event["attempt"], event["latency_ms"], event["input_chars"]) == (1, 840, 36)
    assert (event["prompt_tokens"], event["completion_tokens"]) == (120, 40)
    assert (event["outcome"], event["http_status"]) == ("ok", 200)
    assert "error_category" not in event


def test_a_failed_model_call_is_a_warning_with_its_error_category() -> None:
    r = rig()

    r.telemetry.model_call(
        record(outcome="rate_limited", http_status=429, prompt_tokens=None, completion_tokens=None)
    )

    [event] = r.events
    assert event["level"] == "WARNING"
    assert (event["outcome"], event["error_category"], event["http_status"]) == (
        "rate_limited", "rate_limited", 429,
    )  # fmt: skip
    assert "prompt_tokens" not in event


def test_a_failed_translate_call_names_the_provider_error_and_counts_it() -> None:
    r = rig()

    r.telemetry.model_call(
        record(outcome="rejected", http_status=400, provider_error="invalid_model")
    )
    r.telemetry.model_call(record(outcome="timeout", http_status=None))

    assert r.events[0]["provider_error"] == "invalid_model"
    assert "provider_error" not in r.events[1]
    labels = {"operation": "translate", "provider": "openrouter.ai", "model": "test-model"}
    assert (
        r.count(
            "translator_model_calls_total",
            **labels,
            outcome="rejected",
            provider_error="invalid_model",
        )
        == 1
    )
    assert (
        r.count("translator_model_calls_total", **labels, outcome="timeout", provider_error="none")
        == 1
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_error": "sk-abc secret text"},
        {"outcome": "ok", "provider_error": "invalid_model"},
        {"operation": "detect", "outcome": "timeout", "provider_error": "invalid_model"},
    ],
)
def test_a_provider_error_outside_its_rules_is_dropped(changes: dict[str, Any]) -> None:
    r = rig()

    r.telemetry.model_call(record(**{"outcome": "rejected", "http_status": 400, **changes}))

    assert "provider_error" not in r.events[0]
    assert "sk-abc" not in generate_latest(r.telemetry.registry).decode()


def test_a_detection_call_names_no_prompt_version() -> None:
    r = rig()

    r.telemetry.model_call(
        record(
            operation="detect",
            provider="lingua",
            model="lingua-2.2.0",
            http_status=None,
            prompt_version=None,
        )
    )

    assert "prompt_version" not in r.events[0]


def test_model_calls_feed_the_calls_latency_and_token_metrics() -> None:
    r = rig()
    labels = {"operation": "translate", "provider": "openrouter.ai", "model": "test-model"}

    r.telemetry.model_call(record())
    r.telemetry.model_call(record(outcome="timeout", http_status=None, prompt_tokens=None))

    calls = {**labels, "provider_error": "none"}
    assert r.count("translator_model_calls_total", **calls, outcome="ok") == 1
    assert r.count("translator_model_calls_total", **calls, outcome="timeout") == 1
    assert r.count("translator_model_call_seconds_count", **labels, outcome="ok") == 1
    assert r.count("translator_model_call_seconds_sum", **labels, outcome="ok") == pytest.approx(
        0.84
    )
    assert r.count("translator_model_tokens_total", **labels, outcome="ok", kind="prompt") == 120
    assert r.count("translator_model_tokens_total", **labels, outcome="ok", kind="completion") == 40
    assert (
        r.count("translator_model_tokens_total", **labels, outcome="timeout", kind="prompt") is None
    )


def test_model_call_lines_pick_up_the_correlation_scope_but_an_explicit_id_wins() -> None:
    r = rig()

    with correlation_scope("from-scope"):
        r.telemetry.model_call(record())
        r.telemetry.model_call(record(), correlation_id="explicit")
    r.telemetry.model_call(record())

    assert [e.get("correlation_id") for e in r.events] == ["from-scope", "explicit", None]


def test_a_failed_slack_call_is_a_warning_and_a_good_one_only_a_count() -> None:
    r = rig()

    r.telemetry.slack_call(
        method="chat.postEphemeral", outcome="error", slack_error="channel_not_found"
    )
    r.telemetry.slack_call(method="chat.postEphemeral", outcome="ok")

    [event] = r.events
    assert event["event"] == "slack.call_failed"
    assert event["level"] == "WARNING"
    assert (event["slack_method"], event["slack_error"]) == (
        "chat.postEphemeral", "channel_not_found",
    )  # fmt: skip
    assert (
        r.count("translator_slack_calls_total", method="chat.postEphemeral", outcome="error") == 1
    )
    assert r.count("translator_slack_calls_total", method="chat.postEphemeral", outcome="ok") == 1


def test_a_slack_error_that_is_not_a_code_is_left_out() -> None:
    r = rig()

    r.telemetry.slack_call(
        method="views.open", outcome="error", slack_error="could not post: hello"
    )

    assert "slack_error" not in r.events[0]


def test_acknowledgement_timing_is_a_metric_and_not_a_log_line() -> None:
    r = rig()

    r.telemetry.ack_observed(kind="action", seconds=0.2)

    assert r.lines == []
    assert r.count("translator_ack_seconds_count", kind="action") == 1
    assert r.count("translator_ack_seconds_sum", kind="action") == pytest.approx(0.2)


def test_an_internal_error_logs_the_exception_class_only() -> None:
    r = rig()

    r.telemetry.internal_error(exception_type="KeyError", correlation_id="c1")

    [event] = r.events
    assert (event["event"], event["level"], event["exception_type"]) == (
        "internal.error", "ERROR", "KeyError",
    )  # fmt: skip


def test_an_exception_type_that_is_not_a_name_is_left_out() -> None:
    r = rig()

    r.telemetry.internal_error(exception_type="KeyError: 'secret text'", correlation_id=None)

    assert "exception_type" not in r.events[0]


def test_identifiers_that_look_like_text_are_left_out() -> None:
    r = rig()

    r.telemetry.event_dropped(
        reason="bot", slack_event_id="Ev1 and some customer words", channel_id="C0123ABCDE"
    )

    [event] = r.events
    assert "slack_event_id" not in event
    assert event["channel_id"] == "C0123ABCDE"


def test_the_log_level_filters_lines_but_never_counters() -> None:
    r = rig(log_level="WARNING")

    r.telemetry.startup_ready()
    r.telemetry.event_dropped(reason="bot", slack_event_id=None, channel_id=None)
    r.telemetry.startup_channel_not_member(channel_id="C0123ABCDE")
    r.telemetry.internal_error(exception_type="KeyError", correlation_id=None)

    assert [e["event"] for e in r.events] == ["startup.channel_not_member", "internal.error"]
    assert r.count("translator_events_dropped_total", reason="bot") == 1


def test_error_level_keeps_only_errors() -> None:
    r = rig(log_level="ERROR")

    r.telemetry.startup_channel_not_member(channel_id="C0123ABCDE")
    r.telemetry.internal_error(exception_type="KeyError", correlation_id=None)

    assert [e["event"] for e in r.events] == ["internal.error"]


# LOG-03: libraries that would otherwise print request and event bodies.


@pytest.fixture
def third_party_loggers() -> Iterator[None]:
    names = ("slack_bolt", "slack_sdk", "aiohttp", "httpx", "httpcore")
    saved = [
        (logging.getLogger(n), logging.getLogger(n).level, logging.getLogger(n).handlers[:],
         logging.getLogger(n).propagate)
        for n in names
    ]  # fmt: skip
    yield
    for logger, level, handlers, propagate in saved:
        logger.setLevel(level)
        logger.handlers = handlers
        logger.propagate = propagate


@pytest.mark.usefixtures("third_party_loggers")
def test_third_party_loggers_are_held_at_warning_and_never_print_their_text() -> None:
    r = rig()
    r.telemetry.guard_third_party_loggers()
    marker = "MARKER-do-not-log"

    for name in ("slack_bolt", "slack_sdk", "aiohttp", "httpx", "httpcore"):
        logging.getLogger(name).info("%s %s", marker, name)
        logging.getLogger(name).warning("%s %s", marker, name)
    try:
        raise KeyError(marker)
    except KeyError:
        logging.getLogger("slack_sdk").exception("%s failed", marker)

    assert len(r.events) == 6
    assert marker not in "".join(r.lines)
    assert {e["event"] for e in r.events} == {"third_party.log"}
    assert [e["level"] for e in r.events] == ["WARNING"] * 5 + ["ERROR"]
    assert r.events[-1]["exception_type"] == "KeyError"
    assert [e["logger"] for e in r.events] == [
        "slack_bolt", "slack_sdk", "aiohttp", "httpx", "httpcore", "slack_sdk",
    ]  # fmt: skip
    assert all(set(e) <= ALLOWED_FIELDS for e in r.events)


@pytest.mark.usefixtures("third_party_loggers")
def test_a_child_logger_is_named_by_the_guarded_library() -> None:
    r = rig()
    r.telemetry.guard_third_party_loggers()

    logging.getLogger("slack_bolt.AsyncApp").warning("MARKER-child-text")

    [event] = r.events
    assert event["logger"] == "slack_bolt"
    assert "MARKER-child-text" not in r.lines[0]


TRACE_FIELDS = {
    "correlation_id", "entry", "channel_id", "message_ts", "thread_ts", "slack_event_id",
    "language", "script", "confidence", "attempt", "latency_ms", "input_chars", "outcome",
    "error_category", "refusal_reason", "recipient_count", "author_kind", "slack_method",
}  # fmt: skip


class TestTrace:
    """LOG-04 · One DEBUG event for the path a request took. Never a counter, never a person."""

    def test_nothing_is_written_above_debug(self) -> None:
        r = rig("INFO")
        r.telemetry.trace("received", correlation_id="c1", entry="shortcut")
        assert r.events == []

    def test_at_debug_a_trace_is_one_debug_line_with_its_step_and_the_values_it_was_given(
        self,
    ) -> None:
        r = rig("DEBUG")

        r.telemetry.trace(
            "detection_decided", correlation_id="c1", language="ko", confidence=0.93,
            outcome="hinted", entry="shortcut",
        )  # fmt: skip

        [event] = r.events
        assert (event["event"], event["level"]) == ("trace", "DEBUG")
        assert event["step"] == "detection_decided"
        assert (event["language"], event["confidence"], event["outcome"]) == ("ko", 0.93, "hinted")
        assert event["correlation_id"] == "c1"

    def test_values_that_were_not_given_are_left_out(self) -> None:
        r = rig("DEBUG")
        r.telemetry.trace("admitted", correlation_id="c1")
        [event] = r.events
        assert set(event) == {
            "ts", "level", "event", "app_env", "version", "step", "correlation_id",
        }  # fmt: skip

    def test_a_trace_never_changes_a_counter(self) -> None:
        r = rig("DEBUG")
        before = generate_latest(r.telemetry.registry)
        r.telemetry.trace(
            "delivered_via", correlation_id="c1", outcome="ok", slack_method="response_url"
        )
        r.telemetry.trace(
            "offers_selected", correlation_id="c1", recipient_count=3, author_kind="outside"
        )
        assert generate_latest(r.telemetry.registry) == before

    def test_the_method_takes_exactly_the_allowed_fields_and_no_catch_all(self) -> None:
        parameters = inspect.signature(LogTelemetry.trace).parameters
        assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
        assert set(parameters) - {"self", "step"} == TRACE_FIELDS

    @pytest.mark.parametrize(
        ("step", "outcome"),
        [
            ("member_checked", "cache"), ("member_checked", "lookup"),
            ("member_checked", "payload"),
            ("channel_checked", "list"), ("source_read", "missing"),
            ("detection_decided", "from_offer"), ("detection_decided", "no_offer"),
            ("progress_shown", "skipped"), ("retry_note_shown", "failed"),
            ("output_checked", "escaped"), ("delivered_via", "fallback_ok"),
        ],
    )  # fmt: skip
    def test_each_step_accepts_its_own_outcomes(self, step: Any, outcome: str) -> None:
        r = rig("DEBUG")
        r.telemetry.trace(step, correlation_id="c1", outcome=outcome)
        assert r.events[0]["outcome"] == outcome

    @pytest.mark.parametrize(
        ("step", "outcome"),
        [
            ("member_checked", "ok"), ("source_read", "cache"), ("output_checked", "ok"),
            ("delivered_via", "cache"), ("received", "ok"), ("admitted", "lookup"),
            ("detection_decided", "free text about a message"),
        ],
    )  # fmt: skip
    def test_an_outcome_outside_a_steps_list_is_a_bug_and_raises(
        self, step: Any, outcome: str
    ) -> None:
        r = rig("DEBUG")
        with pytest.raises(ValueError, match="outcome"):
            r.telemetry.trace(step, correlation_id="c1", outcome=outcome)
        assert r.events == []

    def test_identifiers_that_do_not_look_like_identifiers_are_dropped(self) -> None:
        r = rig("DEBUG")
        r.telemetry.trace("received", correlation_id="c1", channel_id="has a space and text")
        assert "channel_id" not in r.events[0]
