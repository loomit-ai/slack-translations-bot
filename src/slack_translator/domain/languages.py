"""IF-01 · Language codes, roles and the CFG-02 seed format."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, NewType

LanguageCode = NewType("LanguageCode", str)
Role = Literal["team", "customer"]
Script = Literal["Hans", "Hant"]

MAX_CONFIGURED_LANGUAGES: Final = 20

_ROLES: Final = ("team", "customer")

# ISO 639-1 code to English name, for the 75 languages Lingua can detect (ADR-005).
# A test compares this table with the installed Lingua so the two cannot drift apart.
SUPPORTED_LANGUAGES: Final[Mapping[str, str]] = {
    "af": "Afrikaans",
    "ar": "Arabic",
    "az": "Azerbaijani",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "bs": "Bosnian",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "eo": "Esperanto",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "ga": "Irish",
    "gu": "Gujarati",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "hy": "Armenian",
    "id": "Indonesian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "ka": "Georgian",
    "kk": "Kazakh",
    "ko": "Korean",
    "la": "Latin",
    "lg": "Ganda",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mi": "Maori",
    "mk": "Macedonian",
    "mn": "Mongolian",
    "mr": "Marathi",
    "ms": "Malay",
    "nb": "Bokmal",
    "nl": "Dutch",
    "nn": "Nynorsk",
    "pa": "Punjabi",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovene",
    "sn": "Shona",
    "so": "Somali",
    "sq": "Albanian",
    "sr": "Serbian",
    "st": "Sotho",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tl": "Tagalog",
    "tn": "Tswana",
    "tr": "Turkish",
    "ts": "Tsonga",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "xh": "Xhosa",
    "yo": "Yoruba",
    "zh": "Chinese",
    "zu": "Zulu",
}


@dataclass(frozen=True, slots=True)
class LanguageCandidate:
    code: LanguageCode
    name: str


@dataclass(frozen=True, slots=True)
class ConfiguredLanguage:
    code: LanguageCode
    name: str
    role: Role


class LanguageConfigError(ValueError):
    """The language configuration is malformed. The message never contains the raw input."""


def parse_language_seed(raw: str) -> tuple[ConfiguredLanguage, ...]:
    """Parse `code:role,code:role,...` (CFG-02). Order is kept; entries are validated one by one."""
    entries = [part.strip() for part in raw.split(",")]
    if entries == [""]:
        raise LanguageConfigError("the language list is empty")

    languages: list[ConfiguredLanguage] = []
    seen: set[str] = set()
    for entry in entries:
        code, separator, role = entry.partition(":")
        if not separator or not code or ":" in role:
            raise LanguageConfigError("each entry must look like code:role")
        if role not in _ROLES:
            raise LanguageConfigError("a role must be team or customer")
        if code not in SUPPORTED_LANGUAGES:
            raise LanguageConfigError("a language code is not supported by the detector")
        if code in seen:
            raise LanguageConfigError("a language code appears twice")
        seen.add(code)
        languages.append(
            ConfiguredLanguage(
                code=LanguageCode(code),
                name=SUPPORTED_LANGUAGES[code],
                role="team" if role == "team" else "customer",
            )
        )

    if len(languages) > MAX_CONFIGURED_LANGUAGES:
        raise LanguageConfigError("too many languages")
    return tuple(languages)


def candidates_of(languages: Iterable[ConfiguredLanguage]) -> tuple[LanguageCandidate, ...]:
    """The `candidates` argument of `LanguageClassifier.detect`: every configured language."""
    return tuple(LanguageCandidate(code=lang.code, name=lang.name) for lang in languages)
