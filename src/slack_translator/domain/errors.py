"""Section 7 (ERR-01 to ERR-10) and IF-04 (errors and call records for both models)."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, get_args

Operation = Literal["detect", "translate"]

# Why a provider turned a translate request down, from a fixed list. It is derived by the
# translator adapter and is the only part of the provider's error answer that leaves it.
ProviderError = Literal[
    "invalid_model",
    "invalid_credentials",
    "insufficient_credits",
    "no_endpoint",
    "context_too_long",
    "content_filtered",
    "invalid_request",
    "rate_limited_account",
    "rate_limited_upstream",
    "unknown",
]
PROVIDER_ERRORS: frozenset[str] = frozenset(get_args(ProviderError))


class ErrorCategory(StrEnum):
    """Every failure ends in exactly one of these. The comment names the contract ID."""

    REFUSED_WORKSPACE = "refused_workspace"  # ERR-01
    REFUSED_CHANNEL = "refused_channel"  # ERR-02
    MESSAGE_UNAVAILABLE = "message_unavailable"  # ERR-03
    NOTHING_TO_TRANSLATE = "nothing_to_translate"  # ERR-04
    TRANSLATION_FAILED = "translation_failed"  # ERR-05
    DETECTION_FAILED = "detection_failed"  # ERR-06
    SLACK_DELIVERY_FAILED = "slack_delivery_failed"  # ERR-07
    CONFIG_INVALID = "config_invalid"  # ERR-08
    INTERNAL = "internal"  # ERR-09
    INPUT_TOO_LONG = "input_too_long"  # ERR-10, reserved (AQ-04)


class ModelErrorCategory(StrEnum):
    TIMEOUT = "timeout"  # retryable
    RATE_LIMITED = "rate_limited"  # HTTP 429, retryable
    UNAVAILABLE = "unavailable"  # HTTP 5xx or connection failure, retryable
    REJECTED = "rejected"  # other HTTP 4xx, not retryable
    INVALID_RESPONSE = "invalid_response"  # empty or malformed answer; one retry (decision 5)


RETRYABLE_MODEL_ERRORS = frozenset(
    {
        ModelErrorCategory.TIMEOUT,
        ModelErrorCategory.RATE_LIMITED,
        ModelErrorCategory.UNAVAILABLE,
        ModelErrorCategory.INVALID_RESPONSE,
    }
)

_MODEL_ERROR_SENTENCES = {
    ModelErrorCategory.TIMEOUT: "the model call timed out",
    ModelErrorCategory.RATE_LIMITED: "the model provider is rate limiting requests",
    ModelErrorCategory.UNAVAILABLE: "the model provider is unavailable",
    ModelErrorCategory.REJECTED: "the model provider rejected the request",
    ModelErrorCategory.INVALID_RESPONSE: "the model answer was not usable",
}


class ModelError(Exception):
    """A failed model call. The message is a fixed sentence per category.

    It never carries the request or the provider's answer: providers echo input in error bodies.
    """

    def __init__(
        self,
        category: ModelErrorCategory,
        operation: Operation,
        http_status: int | None = None,
        retry_after: float | None = None,
        provider_error: ProviderError | None = None,
    ) -> None:
        super().__init__(_MODEL_ERROR_SENTENCES[category])
        self.category = category
        self.operation = operation
        self.http_status = http_status
        self.retry_after = retry_after
        self.provider_error: ProviderError | None = provider_error

    @property
    def retryable(self) -> bool:
        return self.category in RETRYABLE_MODEL_ERRORS


@dataclass(frozen=True, slots=True)
class ModelCallRecord:
    operation: Operation
    provider: str  # "lingua" or the translator host name
    model: str
    attempt: int  # 1 or 2
    latency_ms: int
    outcome: str  # "ok" or a ModelErrorCategory value
    http_status: int | None
    input_chars: int
    prompt_tokens: int | None
    completion_tokens: int | None
    prompt_version: str | None = None  # set by the adapter that owns the prompt (CR-06)
    provider_error: ProviderError | None = None  # failed translate calls that got an answer


class ResponseUrlUnusable(Exception):
    """A Slack response URL cannot be used: its uses or lifetime ran out, or Slack refused it.

    The caller falls back to `PrivateResponder.ephemeral` (Design UI-17, failure row F19).
    """


class SlackDeliveryError(Exception):
    """Slack did not accept a private message (ERR-07). The message never contains the payload."""

    def __init__(self, slack_error: str | None = None, retry_after: float | None = None) -> None:
        super().__init__("Slack did not accept the private message")
        self.slack_error = slack_error
        self.retry_after = retry_after  # seconds Slack asked us to wait, on a rate limit
