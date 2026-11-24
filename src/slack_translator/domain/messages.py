"""Value types that cross the ports (IF-05) and the outcomes a member can be shown.

Outcomes carry facts, never wording. `delivery/views` turns each one into the Block Kit layout
that the Design spec names (ADR-003).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from slack_translator.domain.languages import LanguageCode, Script


@dataclass(frozen=True, slots=True)
class MessageRef:
    channel: str
    ts: str
    thread_root_ts: str | None = None


@dataclass(frozen=True, slots=True)
class SourceMessage:
    ref: MessageRef
    text: str
    user_id: str | None = None


@dataclass(frozen=True, slots=True)
class UserFacts:
    team_id: str | None
    is_bot: bool
    deleted: bool
    is_stranger: bool
    email_domain: str | None


@dataclass(frozen=True, slots=True)
class OfferRequest:
    """What intake hands over once a message is in a configured customer language (FLOW-02).

    Intake has already filtered, deduplicated and classified. It does not choose recipients:
    that is the offer step, which excludes the author and applies the membership rule.
    """

    correlation_id: str
    ref: MessageRef  # channel, message timestamp, thread root when the message is a reply
    author_id: str | None  # never receives the offer; None when Slack gave no author
    language: LanguageCode  # the detected customer language
    script: Script | None  # only for Chinese, when the characters decide it
    received_at: float  # `Clock.now()` when the event arrived; offers expire 60 s after it


@dataclass(frozen=True, slots=True)
class ResponseUrl:
    """A Slack response URL. It is a bearer capability: anyone holding it can post in the channel.

    Its text form is fixed so the value cannot end up in a log line by accident.
    """

    _value: str = field(repr=False)

    def reveal(self) -> str:
        return self._value

    def __str__(self) -> str:
        return "[response-url]"

    def __repr__(self) -> str:
        return "ResponseUrl([response-url])"


@dataclass(frozen=True, slots=True)
class PrivateMessage:
    """What the bot shows one member. Always has a fallback text and blocks (Design UI-18)."""

    text: str
    blocks: Sequence[Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class Translating:
    """UI-03: shown only where the bot can replace its own note."""


@dataclass(frozen=True, slots=True)
class ErrorNotice:
    """Shown in place of the progress note while the bot retries after a failed first attempt."""


@dataclass(frozen=True, slots=True)
class Translated:
    """UI-04 or UI-05: the private translation, naming the source language when it is known."""

    text: str
    source_language: str | None
    source_language_code: LanguageCode | None = None


@dataclass(frozen=True, slots=True)
class AlreadyInReadLanguage:
    """UI-06: detection says the message is already in the reading language."""

    language: str


@dataclass(frozen=True, slots=True)
class NothingToTranslate:
    """ERR-04: fewer than 3 letters after cleaning."""


@dataclass(frozen=True, slots=True)
class MessageUnavailable:
    """UI-07 (ERR-03): the message can no longer be fetched."""


@dataclass(frozen=True, slots=True)
class TranslationFailed:
    """UI-08 (ERR-05). `retry_value` is the SLK-02 value for the Retry button."""

    retry_value: str


@dataclass(frozen=True, slots=True)
class ChannelNotAllowed:
    """UI-09 (ERR-02): an X3M member used the shortcut outside the allowlist."""


ReadOutcome = (
    Translating
    | ErrorNotice
    | Translated
    | AlreadyInReadLanguage
    | NothingToTranslate
    | MessageUnavailable
    | TranslationFailed
    | ChannelNotAllowed
)
