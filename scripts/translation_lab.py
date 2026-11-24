"""US-13 · The shared parts of the terminal tools that try translations outside Slack.

`translate_one.py` and `translate_file.py` use the bot's own translator
(`slack_translator.infrastructure.openai_translator`) unchanged: same prompt, same request. Only
the settings differ, and they come from an env file named on the command line, validated by the
bot's own `Settings` so the rules cannot drift. No Slack token is needed.

Sample and results files hold real message text. They never go into a log line, and the tools
print no message text except the translation the person asked to see.
"""

import csv
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, cast
from urllib.parse import urlsplit

from pydantic import ValidationError

from slack_translator.delivery.settings import Settings, invalid_keys
from slack_translator.domain.detection import Detection
from slack_translator.domain.errors import ModelCallRecord, ModelError
from slack_translator.domain.intake import classify_result
from slack_translator.domain.languages import SUPPORTED_LANGUAGES, LanguageCandidate, LanguageCode
from slack_translator.domain.ports import Telemetry
from slack_translator.domain.text import MIN_LETTERS, clean_for_detection, letter_count
from slack_translator.infrastructure.lingua_classifier import LinguaClassifier
from slack_translator.infrastructure.openai_translator import OpenAITranslator
from slack_translator.infrastructure.system_clock import SystemClock

REPO: Final = Path(__file__).resolve().parents[1]

# The bot's settings need Slack values the tools never use. These fixed placeholders are not
# secrets, and they win over anything in the file, so a real token there is never read.
_PLACEHOLDERS: Final = {
    "CHANNEL_ALLOWLIST": "C00000000",
    "SLACK_BOT_TOKEN": "xoxb-not-used-by-this-tool",
    "SLACK_APP_TOKEN": "xapp-not-used-by-this-tool",
    "APP_ENV": "development",
}

TARGET_TAG: Final = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_MESSAGE_COLUMNS: Final = ("message", "text", "source")

RESULT_COLUMNS: Final = (
    "source",
    "translation",
    "target_language",
    "seconds",
    "worked",
    "error",
    "http_status",
    "slack_tokens",
    "detected_language",
    "lead",
    "bot_decision",
    "model",
    "endpoint",
    "prompt_tokens",
    "completion_tokens",
    "rating",
    "notes",
)

_CODE_BLOCK: Final = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE: Final = re.compile(r"`[^`\n]*`")
_CONTROL: Final = re.compile(r"<(?:[@#!]|https?:|mailto:|tel:)[^<>\n]*>")
_EMOJI: Final = re.compile(r":[A-Za-z+_][A-Za-z0-9_+\-]*:")

_DECISIONS: Final = {
    "customer": "offer (customer language)",
    "team": "no offer (team language)",
    "other": "no offer (other language)",
    "low_confidence": "no offer (lead too low)",
    "error": "no offer (detection failed)",
}


class ProfileError(Exception):
    """A problem the person can fix: a bad env file, a missing sample, an unsafe results path.

    The message names keys and paths and never a setting's value.
    """


def read_env_file(path: Path) -> dict[str, str]:
    """`KEY=VALUE` lines. Comments, blank lines, `export` and surrounding quotes are handled."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ProfileError(f"env file not found: {path}") from None
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    settings: Settings

    @property
    def model(self) -> str:
        return self.settings.translator_model

    @property
    def host(self) -> str:
        return urlsplit(self.settings.translator_base_url).hostname or "unknown"


def load_profile(path: Path) -> Profile:
    values = {**read_env_file(path), **_PLACEHOLDERS}
    try:
        settings = Settings.from_environ(values)
    except ValidationError as error:
        names = ", ".join(invalid_keys(error))
        raise ProfileError(f"missing or invalid settings in {path.name}: {names}") from None
    return Profile(name=path.stem, settings=settings)


@dataclass(frozen=True, slots=True)
class TokenCheck:
    """Slack tokens of the source that the translation lost or changed, and control sequences it
    added (step 9 says to leave mentions, links, emoji codes and code untouched)."""

    missing: tuple[str, ...]
    added: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.added

    def describe(self) -> str:
        def shown(tokens: Sequence[str]) -> str:
            return ", ".join(token.replace("\n", "\\n") for token in tokens)

        parts: list[str] = []
        if self.missing:
            parts.append(f"missing: {shown(self.missing)}")
        if self.added:
            parts.append(f"added: {shown(self.added)}")
        return "; ".join(parts) or "ok"


def _scan(text: str) -> tuple[Counter[str], Counter[str]]:
    """The protected tokens of a text, and the Slack control sequences outside code."""
    protected: Counter[str] = Counter()
    controls: Counter[str] = Counter()
    rest = text
    for pattern in (_CODE_BLOCK, _INLINE_CODE):
        protected.update(match.group(0) for match in pattern.finditer(rest))
        rest = pattern.sub(" ", rest)
    found = [match.group(0) for match in _CONTROL.finditer(rest)]
    protected.update(found)
    controls.update(found)
    rest = _CONTROL.sub(" ", rest)
    protected.update(match.group(0) for match in _EMOJI.finditer(rest))
    return protected, controls


def check_slack_tokens(source: str, translation: str) -> TokenCheck:
    source_tokens, source_controls = _scan(source)
    translated_tokens, translated_controls = _scan(translation)
    missing = list((source_tokens - translated_tokens).elements())
    added = list((translated_controls - source_controls).elements())
    return TokenCheck(missing=tuple(missing), added=tuple(added))


def read_messages(path: Path) -> list[str]:
    """One message per line, or a CSV (a `message`, `text` or `source` column, else the first)."""
    try:
        if path.suffix.lower() == ".csv":
            messages = _csv_messages(path)
        else:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            messages = [line for line in lines if line.strip()]
    except FileNotFoundError:
        raise ProfileError(f"sample file not found: {path}") from None
    if not messages:
        raise ProfileError(f"no messages in {path}")
    return messages


def _csv_messages(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return []
    header = [cell.strip().lower() for cell in rows[0]]
    column = next((header.index(name) for name in _MESSAGE_COLUMNS if name in header), None)
    body = rows[1:] if column is not None else rows
    index = column if column is not None else 0
    return [row[index] for row in body if len(row) > index and row[index].strip()]


def ensure_private_path(path: Path) -> None:
    """Refuse a results path inside the repository that git would not ignore."""
    resolved = path.resolve()
    if not resolved.is_relative_to(REPO):
        return
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO), "check-ignore", "-q", "--", str(resolved)],
            capture_output=True,
            check=False,
        )
    except OSError:
        return
    if result.returncode == 1:
        raise ProfileError(
            f"{path} is inside the repository and not ignored by git; "
            "use the lab/ folder or a place outside the repository"
        )


def default_results_path(sample: Path, profile: Profile, target: str, now: datetime) -> Path:
    stamp = f"{now:%Y%m%d-%H%M%S}"
    return REPO / "lab" / "results" / f"{sample.stem}-{profile.name}-{target}-{stamp}.csv"


@dataclass(frozen=True, slots=True)
class Outcome:
    source: str
    translation: str
    target: str
    seconds: float
    worked: bool
    error: str = ""
    http_status: int | None = None
    check: TokenCheck = TokenCheck((), ())
    detected_code: str | None = None
    detected_name: str | None = None
    lead: float | None = None
    decision: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def write_results(path: Path, outcomes: Sequence[Outcome], profile: Profile) -> None:
    """One row per message, with empty `rating` and `notes` columns for a person to fill in
    (usable, needs edits or wrong). UTF-8 with a byte-order mark so a spreadsheet reads Korean."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(RESULT_COLUMNS)
        for outcome in outcomes:
            writer.writerow(
                [
                    outcome.source,
                    outcome.translation,
                    outcome.target,
                    f"{outcome.seconds:.2f}",
                    "yes" if outcome.worked else "no",
                    outcome.error,
                    "" if outcome.http_status is None else outcome.http_status,
                    outcome.check.describe() if outcome.worked else "",
                    outcome.detected_code or "",
                    "" if outcome.lead is None else f"{outcome.lead:.3f}",
                    outcome.decision,
                    profile.model,
                    profile.host,
                    "" if outcome.prompt_tokens is None else outcome.prompt_tokens,
                    "" if outcome.completion_tokens is None else outcome.completion_tokens,
                    "",
                    "",
                ]
            )


class _Records:
    """A telemetry that only keeps the model call records, to read time and tokens from."""

    def __init__(self) -> None:
        self.records: list[ModelCallRecord] = []

    def model_call(self, record: ModelCallRecord, *, correlation_id: str | None = None) -> None:
        self.records.append(record)


_ALL_LANGUAGES: Final = tuple(
    LanguageCandidate(LanguageCode(code), name) for code, name in SUPPORTED_LANGUAGES.items()
)


class Lab:
    """Runs messages through the bot's own translator and, if asked, Lingua."""

    def __init__(self, profile: Profile, *, detect: bool = True, hint: str = "auto") -> None:
        self._profile = profile
        self._hint = hint
        self._records = _Records()
        telemetry = cast(Telemetry, self._records)
        settings = profile.settings
        self._translator = OpenAITranslator(
            base_url=settings.translator_base_url,
            model=settings.translator_model,
            api_key=settings.translator_api_key,
            timeout=settings.translator_timeout,
            telemetry=telemetry,
            clock=SystemClock(),
            extra_body=settings.translator_extra_body,
        )
        self._classifier = (
            LinguaClassifier(telemetry=telemetry, clock=SystemClock(), timeout=30.0)
            if detect
            else None
        )

    async def start(self) -> None:
        if self._classifier is not None:
            await self._classifier.preload()

    async def aclose(self) -> None:
        await self._translator.aclose()
        if self._classifier is not None:
            await self._classifier.aclose()

    async def run(self, text: str, target: str) -> Outcome:
        """Translate one message. Raises `ValueError` for a blank text or a bad target."""
        code, name, lead, decision = await self._detect(text)
        hint = self._choose_hint(code, decision)

        started = time.perf_counter()
        translation = ""
        error = ""
        http_status: int | None = None
        before = len(self._records.records)
        try:
            translation = await self._translator.translate(text, target, hint)
        except ModelError as failure:
            error = failure.category.value
            http_status = failure.http_status
        seconds = time.perf_counter() - started

        record = next(
            (r for r in self._records.records[before:] if r.operation == "translate"), None
        )
        if record is not None and http_status is None:
            http_status = record.http_status
        worked = error == ""
        return Outcome(
            source=text,
            translation=translation,
            target=target,
            seconds=seconds,
            worked=worked,
            error=error,
            http_status=http_status,
            check=check_slack_tokens(text, translation) if worked else TokenCheck((), ()),
            detected_code=code,
            detected_name=name,
            lead=lead,
            decision=decision,
            prompt_tokens=record.prompt_tokens if record else None,
            completion_tokens=record.completion_tokens if record else None,
        )

    async def _detect(self, text: str) -> tuple[str | None, str | None, float | None, str]:
        if self._classifier is None:
            return None, None, None, ""
        cleaned = clean_for_detection(text)
        if letter_count(cleaned) < MIN_LETTERS:
            return None, None, None, "no offer (too few letters)"
        try:
            detection = await self._classifier.detect(cleaned, _ALL_LANGUAGES)
        except ModelError:
            return None, None, None, _DECISIONS["error"]
        if detection.language == "other":
            return None, None, detection.confidence, _DECISIONS["other"]
        settings = self._profile.settings
        result = classify_result(
            Detection(detection.language, detection.confidence),
            settings.language_seed,
            settings.classifier_min_confidence,
        )
        decision = _DECISIONS[result]
        if result == "low_confidence":
            decision = f"no offer (lead below {settings.classifier_min_confidence:g})"
        return (
            detection.language,
            SUPPORTED_LANGUAGES[detection.language],
            detection.confidence,
            decision,
        )

    def _choose_hint(self, code: str | None, decision: str) -> LanguageCode | None:
        """`auto` hints like the bot: a configured language known with enough lead."""
        if self._hint == "none":
            return None
        if self._hint != "auto":
            return LanguageCode(self._hint)
        if code is not None and (decision.startswith("offer") or "team language" in decision):
            return LanguageCode(code)
        return None


def validate_target(target: str) -> str:
    if not TARGET_TAG.fullmatch(target):
        raise ProfileError("the target language must be a code such as en, ko or zh-Hant")
    return target


def use_utf8_output() -> None:
    """Korean must print correctly whatever the terminal's default encoding is."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
