"""Language detection. Uses langdetect; falls back to 'unknown' on failure."""
from __future__ import annotations

from dataclasses import dataclass

from langdetect import DetectorFactory, LangDetectException, detect_langs

# Deterministic output across runs
DetectorFactory.seed = 0


@dataclass
class LanguageResult:
    language: str
    confidence: float


def detect_language(text: str) -> LanguageResult:
    """Detect language of a text. Returns 'unknown' with 0.0 confidence on failure."""
    if not text or len(text.strip()) < 3:
        return LanguageResult(language="unknown", confidence=0.0)
    try:
        # Return the top result only
        results = detect_langs(text)
        if not results:
            return LanguageResult(language="unknown", confidence=0.0)
        top = results[0]
        return LanguageResult(language=top.lang, confidence=float(top.prob))
    except LangDetectException:
        return LanguageResult(language="unknown", confidence=0.0)
