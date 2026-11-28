"""FLOW-02 steps 1 and 4 · Which message events are classified, and what a result means.

Pure functions. The decision about channels sits behind one predicate that `delivery` hands
over, so a change in how channels are chosen changes one function and none of these rules.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from slack_translator.domain.detection import Detection
from slack_translator.domain.languages import ConfiguredLanguage
from slack_translator.domain.ports import ClassifyResult, DropReason
from slack_translator.domain.text import MIN_LETTERS, clean_for_detection, letter_count

# The app subscribes to public and private channels only (message.channels, message.groups).
CHANNEL_TYPES: Final = frozenset({"channel", "group"})

# A message with one of these subtypes is a human message; any other subtype is an edit, a
# deletion, a join, a bot post and so on.
CLASSIFIED_SUBTYPES: Final = frozenset({"file_share", "thread_broadcast"})


@dataclass(frozen=True, slots=True)
class MessageEvent:
    """The facts of a Slack `message` event that intake needs. `user_id` is the author."""

    event_id: str | None
    channel: str
    ts: str
    thread_root_ts: str | None
    user_id: str | None
    text: str | None
    subtype: str | None
    bot_id: str | None
    channel_type: str | None = None  # `channel`, `group`, or what Slack calls a direct message


@dataclass(frozen=True, slots=True)
class Dropped:
    reason: DropReason


@dataclass(frozen=True, slots=True)
class Admitted:
    """The event passed every filter. `cleaned_text` is what detection sees (REQ-07)."""

    cleaned_text: str


def _is_channel(event: MessageEvent) -> bool:
    """Public or private channel. Without a conversation type, the ID prefix decides."""
    if event.channel_type is not None:
        return event.channel_type in CHANNEL_TYPES
    return event.channel.startswith(("C", "G"))


def screen(event: MessageEvent, is_served: Callable[[str], bool]) -> Dropped | Admitted:
    """The filters of FLOW-02 step 1, in order; the first that matches is the reason counted.

    The duplicate check is not here because it needs state. A direct message is dropped before
    the served check, which can have the side effect of remembering the channel as served.
    """
    if not _is_channel(event) or not is_served(event.channel):
        return Dropped("not_allowlisted")
    if event.subtype is not None and event.subtype not in CLASSIFIED_SUBTYPES:
        return Dropped("subtype")
    if event.bot_id is not None:
        return Dropped("bot")
    if event.text is None or not event.text.strip():
        return Dropped("empty")
    cleaned = clean_for_detection(event.text)
    if letter_count(cleaned) < MIN_LETTERS:
        return Dropped("too_short")
    return Admitted(cleaned_text=cleaned)


def known_language(detection: Detection | None, languages: Sequence[ConfiguredLanguage]) -> str:
    """The language code to count, or `other` when it is not a configured one (MET-01)."""
    if detection is not None and any(lang.code == detection.language for lang in languages):
        return detection.language
    return "other"


def classify_result(
    detection: Detection | None,
    languages: Sequence[ConfiguredLanguage],
    min_confidence: float,
) -> ClassifyResult:
    """`detection` is `None` when detection failed or timed out."""
    if detection is None:
        return "error"
    configured = next((lang for lang in languages if lang.code == detection.language), None)
    if configured is None:
        return "other"
    if detection.confidence < min_confidence:
        return "low_confidence"
    return configured.role


def customer_language(
    detection: Detection | None,
    languages: Sequence[ConfiguredLanguage],
    min_confidence: float,
) -> ConfiguredLanguage | None:
    """The language to offer for, or `None` when nothing should be offered (FLOW-02 step 5)."""
    if detection is None or classify_result(detection, languages, min_confidence) != "customer":
        return None
    return next(lang for lang in languages if lang.code == detection.language)
