"""IF-02 · The result of language detection. Offer and shortcut rules join this module later."""

from dataclasses import dataclass
from typing import Literal

from slack_translator.domain.languages import LanguageCode, Script


@dataclass(frozen=True, slots=True)
class Detection:
    language: LanguageCode | Literal["other"]
    confidence: float  # 0.0 to 1.0 inclusive
    script: Script | None = None  # only when language == "zh"
