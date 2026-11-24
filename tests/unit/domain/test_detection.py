"""FLOW-01 step 5 and the Product Owner's answers: what a detection result means on the shortcut."""

import pytest

from slack_translator.domain.detection import (
    AlreadyRead,
    Detection,
    Hinted,
    Unhinted,
    decide_read,
)
from slack_translator.domain.languages import LanguageCode, parse_language_seed

LANGUAGES = parse_language_seed("en:team,es:team,ko:customer,zh:customer")
READ = LanguageCode("en")


def decide(detection: Detection | None, threshold: float = 0.8):
    return decide_read(detection, LANGUAGES, READ, threshold)


def detected(language: str, confidence: float, script: str | None = None) -> Detection:
    return Detection(LanguageCode(language), confidence, script)  # type: ignore[arg-type]


def test_the_reading_language_at_the_threshold_is_already_read() -> None:
    assert decide(detected("en", 0.8)) == AlreadyRead(language="English")
    assert decide(detected("en", 0.99)) == AlreadyRead(language="English")


def test_the_reading_language_below_the_threshold_is_not_trusted() -> None:
    assert decide(detected("en", 0.79)) == Unhinted()


def test_a_configured_language_at_the_threshold_becomes_the_hint() -> None:
    assert decide(detected("ko", 0.9)) == Hinted(
        code=LanguageCode("ko"), name="Korean", script=None
    )


def test_a_team_language_other_than_the_reading_language_is_translated_and_named() -> None:
    assert decide(detected("es", 0.95)) == Hinted(
        code=LanguageCode("es"), name="Spanish", script=None
    )


def test_the_chinese_script_travels_with_the_hint() -> None:
    assert decide(detected("zh", 0.9, "Hant")) == Hinted(
        code=LanguageCode("zh"), name="Chinese", script="Hant"
    )


@pytest.mark.parametrize(
    "detection",
    [None, detected("ko", 0.5), Detection("other", 0.99), detected("fr", 0.99)],
)
def test_no_answer_low_confidence_other_or_unlisted_means_no_hint(
    detection: Detection | None,
) -> None:
    assert decide(detection) == Unhinted()
