"""IF-02 and ADR-005 · `LanguageClassifier` with Lingua.

The only module that imports `lingua`. The decision set is every language Lingua supports, so a
language outside `candidates` can come back as `other` instead of being forced into a candidate.
Nothing loads at import. Detection runs in worker threads, at most `max_in_flight` at once, under
a deadline of `CLASSIFIER_TIMEOUT`. Text is never stored or logged.
"""

import asyncio
import importlib.metadata
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Final

from lingua import LanguageDetector, LanguageDetectorBuilder

from slack_translator.domain.detection import Detection
from slack_translator.domain.errors import ModelCallRecord, ModelError, ModelErrorCategory
from slack_translator.domain.languages import LanguageCandidate, LanguageCode
from slack_translator.domain.ports import Clock, Telemetry

# Ranked (ISO 639-1 code, confidence) pairs, best first. A seam for tests; Lingua by default.
Scorer = Callable[[str], Sequence[tuple[str, float]]]

DEFAULT_MAX_IN_FLIGHT: Final = 4

# Lingua's own confidence differs in the 15th decimal between identical calls. Rounding keeps
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
        return _to_detection(ranked, candidates)

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


def _to_detection(
    ranked: Sequence[tuple[str, float]], candidates: Sequence[LanguageCandidate]
) -> Detection:
    """The top language if it is a candidate, otherwise `other`. No signal is `other` at 0.0."""
    if not ranked:
        return Detection(language="other", confidence=0.0)
    code, value = ranked[0]
    confidence = round(min(max(value, 0.0), 1.0), CONFIDENCE_DECIMALS)
    if confidence == 0.0:
        return Detection(language="other", confidence=0.0)
    if any(candidate.code == code for candidate in candidates):
        return Detection(language=LanguageCode(code), confidence=confidence)
    return Detection(language="other", confidence=confidence)
