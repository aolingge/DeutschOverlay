"""Pure transcription data: what the recognizer produced, with source timings.

These types deliberately carry time in *source* coordinates. The recognizer
reports segment and word times relative to the audio it decoded, plus the
absolute sample offset of that audio inside a session. Consumers map those
sample offsets onto media time; nothing here uses a wall clock, so an
inference finishing late can never move a caption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    """One model-produced word with model-produced start and end times."""

    text: str
    start_ms: int
    end_ms: int
    start_sample: int | None = None
    end_sample: int | None = None
    probability: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "startSample": self.start_sample,
            "endSample": self.end_sample,
            "probability": self.probability,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TranscriptWord":
        if not isinstance(data, dict):
            raise TypeError("word must be an object")
        return cls(
            text=_as_text(data.get("text")),
            start_ms=_as_optional_int(data.get("startMs")) or 0,
            end_ms=_as_optional_int(data.get("endMs")) or 0,
            start_sample=_as_optional_int(data.get("startSample")),
            end_sample=_as_optional_int(data.get("endSample")),
            probability=_as_optional_float(data.get("probability")),
        )


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """One recognized span, timed against the audio that was submitted.

    ``start_sample``/``end_sample`` are absolute positions in the session's
    submitted-audio stream. They are ``None`` only when the caller did not
    know where the clip came from, which makes the segment unusable for
    web-video timings and is reported rather than silently zeroed.
    """

    text: str
    language: str
    start_ms: int
    end_ms: int
    start_sample: int | None = None
    end_sample: int | None = None
    words: tuple[TranscriptWord, ...] = ()
    no_speech_probability: float | None = None
    avg_logprob: float | None = None
    raw_text: str = ""

    @property
    def has_model_words(self) -> bool:
        return bool(self.words)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "startSample": self.start_sample,
            "endSample": self.end_sample,
            "words": [word.to_dict() for word in self.words],
            "noSpeechProbability": self.no_speech_probability,
            "avgLogprob": self.avg_logprob,
            "rawText": self.raw_text or self.text,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TranscriptSegment":
        if not isinstance(data, dict):
            raise TypeError("segment must be an object")
        words = data.get("words") or []
        if not isinstance(words, (list, tuple)):
            raise TypeError("words must be a list")
        return cls(
            text=_as_text(data.get("text")),
            language=_as_text(data.get("language")),
            start_ms=_as_optional_int(data.get("startMs")) or 0,
            end_ms=_as_optional_int(data.get("endMs")) or 0,
            start_sample=_as_optional_int(data.get("startSample")),
            end_sample=_as_optional_int(data.get("endSample")),
            words=tuple(TranscriptWord.from_dict(word) for word in words),
            no_speech_probability=_as_optional_float(data.get("noSpeechProbability")),
            avg_logprob=_as_optional_float(data.get("avgLogprob")),
            raw_text=_as_text(data.get("rawText")),
        )


@dataclass(frozen=True, slots=True)
class TranscriptTranslation:
    """A translation of one segment, kept separate from recognition.

    Recognition must never wait for this: a segment is published with its
    original text first, and a translation revision follows if it succeeds.
    ``failed`` records that the attempt failed so the consumer keeps showing
    the original instead of an empty line.
    """

    language: str
    text: str
    backend: str
    failed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "text": self.text,
            "backend": self.backend,
            "failed": self.failed,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TranscriptTranslation":
        if not isinstance(data, dict):
            raise TypeError("translation must be an object")
        return cls(
            language=_as_text(data.get("language")),
            text=data.get("text") if isinstance(data.get("text"), str) else "",
            backend=_as_text(data.get("backend")),
            failed=bool(data.get("failed")),
        )


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    """A complete transcription pass over a bounded piece of audio."""

    segments: tuple[TranscriptSegment, ...] = ()
    language: str = ""
    language_probability: float | None = None
    duration_seconds: float | None = None
    warning: str | None = None
    device: str = ""

    @property
    def text(self) -> str:
        return " ".join(segment.text for segment in self.segments if segment.text).strip()

    @classmethod
    def of(cls, segments: Iterable[TranscriptSegment], **kwargs: Any) -> "TranscriptResult":
        return cls(segments=tuple(segments), **kwargs)
