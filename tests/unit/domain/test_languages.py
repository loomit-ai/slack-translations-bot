"""IF-01 and CFG-02: language codes, roles and seed parsing."""

import pytest
from lingua import Language

from slack_translator.domain.languages import (
    MAX_CONFIGURED_LANGUAGES,
    SUPPORTED_LANGUAGES,
    ConfiguredLanguage,
    LanguageCandidate,
    LanguageCode,
    LanguageConfigError,
    candidates_of,
    parse_language_seed,
)

DEFAULT_SEED = "en:team,es:team,ru:customer,ko:customer,vi:customer,zh:customer"


def test_supported_languages_match_what_lingua_can_detect() -> None:
    lingua_codes = {lang.iso_code_639_1.name.lower() for lang in Language.all()}
    assert set(SUPPORTED_LANGUAGES) == lingua_codes


def test_default_seed_parses_in_order_with_roles_and_names() -> None:
    languages = parse_language_seed(DEFAULT_SEED)
    assert languages == (
        ConfiguredLanguage(LanguageCode("en"), "English", "team"),
        ConfiguredLanguage(LanguageCode("es"), "Spanish", "team"),
        ConfiguredLanguage(LanguageCode("ru"), "Russian", "customer"),
        ConfiguredLanguage(LanguageCode("ko"), "Korean", "customer"),
        ConfiguredLanguage(LanguageCode("vi"), "Vietnamese", "customer"),
        ConfiguredLanguage(LanguageCode("zh"), "Chinese", "customer"),
    )


def test_whitespace_around_entries_is_ignored() -> None:
    languages = parse_language_seed(" en:team , ko:customer ")
    assert [lang.code for lang in languages] == ["en", "ko"]


def test_candidates_carry_code_and_name_for_both_roles() -> None:
    candidates = candidates_of(parse_language_seed("en:team,ko:customer"))
    assert candidates == (
        LanguageCandidate(LanguageCode("en"), "English"),
        LanguageCandidate(LanguageCode("ko"), "Korean"),
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "en",
        "en:",
        ":team",
        "en:team,",
        "en:admin",
        "EN:team",
        "eng:team",
        "xx:customer",
        "en:team,en:customer",
        "en:team:extra",
    ],
)
def test_malformed_seed_is_rejected(raw: str) -> None:
    with pytest.raises(LanguageConfigError):
        parse_language_seed(raw)


def test_seed_with_more_than_the_limit_is_rejected() -> None:
    codes = sorted(SUPPORTED_LANGUAGES)[: MAX_CONFIGURED_LANGUAGES + 1]
    raw = ",".join(f"{code}:customer" for code in codes)
    with pytest.raises(LanguageConfigError):
        parse_language_seed(raw)


def test_seed_at_the_limit_is_accepted() -> None:
    codes = sorted(SUPPORTED_LANGUAGES)[:MAX_CONFIGURED_LANGUAGES]
    raw = ",".join(f"{code}:customer" for code in codes)
    assert len(parse_language_seed(raw)) == MAX_CONFIGURED_LANGUAGES
