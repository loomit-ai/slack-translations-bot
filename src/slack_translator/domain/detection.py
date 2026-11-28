"""IF-02 · The detection result, and what it means on the message-menu path (FLOW-01 step 5)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from slack_translator.domain.languages import ConfiguredLanguage, LanguageCode, Script


@dataclass(frozen=True, slots=True)
class Detection:
    language: LanguageCode | Literal["other"]
    confidence: float  # 0.0 to 1.0 inclusive
    script: Script | None = None  # only when language == "zh"


@dataclass(frozen=True, slots=True)
class AlreadyRead:
    """The message is already in the reading language, with enough confidence to say so."""

    language: str


@dataclass(frozen=True, slots=True)
class Hinted:
    """A configured language, with enough confidence to name it and to hint the translator."""

    code: LanguageCode
    name: str
    script: Script | None


@dataclass(frozen=True, slots=True)
class Unhinted:
    """No usable answer: translate anyway, name no language (Product Owner, 24 Nov 2026)."""


ReadDecision = AlreadyRead | Hinted | Unhinted


def decide_read(
    detection: Detection | None,
    languages: Sequence[ConfiguredLanguage],
    read_language: LanguageCode,
    min_confidence: float,
) -> ReadDecision:
    """`detection` is `None` when detection failed. Below the threshold counts as no answer."""
    if detection is None or detection.confidence < min_confidence:
        return Unhinted()
    configured = next((lang for lang in languages if lang.code == detection.language), None)
    if configured is None:
        return Unhinted()
    if configured.code == read_language:
        return AlreadyRead(language=configured.name)
    return Hinted(code=configured.code, name=configured.name, script=detection.script)
