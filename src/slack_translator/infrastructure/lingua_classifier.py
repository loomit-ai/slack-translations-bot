"""IF-02 and ADR-005 · `LanguageClassifier` with Lingua.

The only module that imports `lingua`. The decision set is every language Lingua supports, so a
language outside `candidates` can come back as `other` instead of being forced into a candidate.
Nothing loads at import. Detection runs in worker threads, at most `max_in_flight` at once, under
a deadline of `CLASSIFIER_TIMEOUT`. Text is never stored or logged. `confidence` is the lead of the
top language over the runner-up (decision 4, CR-10), not Lingua's raw top value. `script` is set
for Chinese from the characters (ADR-005).
"""

import asyncio
import importlib.metadata
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from math import isfinite
from typing import Final, cast

from lingua import LanguageDetector, LanguageDetectorBuilder

from slack_translator.domain.chinese_script import script_of
from slack_translator.domain.detection import Detection
from slack_translator.domain.errors import ModelCallRecord, ModelError, ModelErrorCategory
from slack_translator.domain.languages import LanguageCandidate, LanguageCode
from slack_translator.domain.ports import Clock, Telemetry

# Ranked (ISO 639-1 code, confidence) pairs, best first. A seam for tests; Lingua by default.
Scorer = Callable[[str], Sequence[tuple[str, float]]]

DEFAULT_MAX_IN_FLIGHT: Final = 4

# Lingua's own values differ in the 15th decimal between identical calls. Rounding the lead keeps
# IF-02's "same text, same result" and keeps a threshold check from flipping on noise.
CONFIDENCE_DECIMALS: Final = 6

# Made-up sentences in the languages that matter first. Lingua 2.2 loads a language's model when
# a text first needs it, so `preload` reads these once to take that cost out of the first message.
_WARM_UP_TEXTS: Final = (
    "I put three paper stars in the blue box.",
    "Puse tres estrellas de papel en la caja azul.",
    "La boîte bleue contient trois étoiles en papier.",
    "В синей коробке лежат три бумажные звезды.",
    "파란 상자에 종이 별이 세 개 있어요.",
    "Có ba ngôi sao giấy trong hộp màu xanh.",
    "请把三颗纸星星放进蓝色盒子里。",
    "青い箱に紙の星が三つ入っています。",
)


class LinguaClassifier:
    def __init__(
        self,
        *,
        telemetry: Telemetry,
        clock: Clock,
        timeout: float,
        max_in_flight: int = DEFAULT_MAX_IN_FLIGHT,
        scorer: Scorer | None = None,
    ) -> None:
        self._telemetry = telemetry
        self._clock = clock
        self._timeout = timeout
        self._scorer = scorer
        self._detector: LanguageDetector | None = None
        self._load_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_in_flight, thread_name_prefix="lingua")
        self._model = f"lingua-{importlib.metadata.version('lingua-language-detector')}"

    @property
    def is_loaded(self) -> bool:
        return self._scorer is not None or self._detector is not None

    async def preload(self) -> None:
        """Build the detector and warm it up, off the event loop. Safe to call twice."""
        await asyncio.get_running_loop().run_in_executor(self._executor, self._warm_up)

    async def aclose(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def detect(self, text: str, candidates: Sequence[LanguageCandidate]) -> Detection:
        started = self._clock.now()
        failure: ModelErrorCategory | None = None
        ranked: Sequence[tuple[str, float]] = ()
        try:
            async with asyncio.timeout(self._timeout):
                ranked = await asyncio.get_running_loop().run_in_executor(
                    self._executor, self._rank, text
                )
        except TimeoutError:
            failure = ModelErrorCategory.TIMEOUT
        except Exception:
            failure = ModelErrorCategory.INVALID_RESPONSE
        # Raised outside the handlers so the detector's own exception, which can quote the
        # text, is not attached to it.
        outcome = "ok" if failure is None else failure.value
        self._telemetry.model_call(
            ModelCallRecord(
                operation="detect",
                provider="lingua",
                model=self._model,
                attempt=1,
                latency_ms=max(0, round((self._clock.now() - started) * 1000)),
                outcome=outcome,
                http_status=None,
                input_chars=len(text),
                prompt_tokens=None,
                completion_tokens=None,
            )
        )
        if failure is not None:
            raise ModelError(failure, "detect")
        return _with_script(_to_detection(ranked, candidates), text)

    def _rank(self, text: str) -> Sequence[tuple[str, float]]:
        if self._scorer is not None:
            return self._scorer(text)
        detector = self._load()
        return [
            (value.language.iso_code_639_1.name.lower(), value.value)
            for value in detector.compute_language_confidence_values(text)
        ]

    def _load(self) -> LanguageDetector:
        with self._load_lock:
            if self._detector is None:
                self._detector = (
                    LanguageDetectorBuilder.from_all_languages()
                    .with_preloaded_language_models()
                    .build()
                )
            return self._detector

    def _warm_up(self) -> None:
        if self._scorer is not None:
            return
        detector = self._load()
        for text in _WARM_UP_TEXTS:
            detector.compute_language_confidence_values(text)


def _with_script(detection: Detection, text: str) -> Detection:
    """ADR-005: Lingua has one Chinese entry, so the script comes from the characters."""
    if detection.language != "zh":
        return detection
    return replace(detection, script=script_of(text))


def _entry(item: object) -> tuple[str, float] | None:
    """A (language code, finite number) pair, or None for anything else."""
    if not isinstance(item, tuple | list):
        return None
    parts = cast(Sequence[object], item)
    if len(parts) != 2:
        return None
    code, value = parts
    if not isinstance(code, str) or isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return (code, float(value)) if isfinite(value) else None


def _to_detection(ranked: Sequence[object], candidates: Sequence[LanguageCandidate]) -> Detection:
    """CR-10: `confidence` is the lead of the top language, top / (top + second).

    It runs from 0.5 (a tie) to 1.0 (no rival). A missing or zero second value is 1.0. Nothing
    ranked, a top of 0, or an entry that is not a (code, number) pair is `other` at 0.0. A top
    language outside `candidates` is `other` with the same lead.
    """
    no_signal = Detection(language="other", confidence=0.0)
    if not ranked:
        return no_signal
    first = _entry(ranked[0])
    runner_up = _entry(ranked[1]) if len(ranked) > 1 else ("", 0.0)
    if first is None or runner_up is None or first[1] <= 0:
        return no_signal
    code, top = first
    second = runner_up[1]
    confidence = 1.0 if second <= 0 else round(top / (top + second), CONFIDENCE_DECIMALS)
    if any(candidate.code == code for candidate in candidates):
        return Detection(language=LanguageCode(code), confidence=confidence)
    return Detection(language="other", confidence=confidence)
