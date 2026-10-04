"""Offline speech recognition and German translation."""

from __future__ import annotations

import time
import math
from pathlib import Path

import numpy as np

from deutsch_overlay.captions import CaptionEvent
from deutsch_overlay.gpu_runtime import prepare_cuda_dlls
from deutsch_overlay.models import ModelStore
from deutsch_overlay.transcript import (
    TranscriptResult,
    TranscriptSegment,
    TranscriptTranslation,
    TranscriptWord,
)

SAMPLE_RATE = 16000
SUPPORTED_LANGUAGES = ("de", "en", "zh")


class OpusTranslator:
    """Run a prepared MarianMT model through CTranslate2 on CPU."""

    def __init__(self, model_path: Path) -> None:
        import ctranslate2
        from transformers import MarianTokenizer

        self.tokenizer = MarianTokenizer.from_pretrained(str(model_path), local_files_only=True)
        self.translator = ctranslate2.Translator(str(model_path), device="cpu", compute_type="int8")

    def translate(self, text: str) -> str:
        token_ids = self.tokenizer.encode(text)
        source = self.tokenizer.convert_ids_to_tokens(token_ids)
        result = self.translator.translate_batch([source], beam_size=4)[0]
        target_ids = self.tokenizer.convert_tokens_to_ids(result.hypotheses[0])
        return self.tokenizer.decode(target_ids, skip_special_tokens=True).strip()


def _whisper_factory(path: Path, *, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    return WhisperModel(str(path), device=device, compute_type=compute_type)


def _optional_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    return None


class LocalEngine:
    def __init__(
        self,
        *,
        asr=None,
        translators: dict[str, object] | None = None,
        model_store: ModelStore | None = None,
        asr_factory=None,
        prefer_gpu: bool = True,
    ) -> None:
        self.asr = asr
        self.translators = dict(translators or {})
        self.store = model_store or ModelStore()
        self.asr_factory = asr_factory or _whisper_factory
        self.prefer_gpu = prefer_gpu
        self.warning: str | None = None
        self._warmed = False
        self._using_gpu = False

    def prepare(self) -> None:
        """Load models before capture so the first spoken sentence is not queued cold."""
        self._get_asr()
        self._get_translator("en")
        self._get_translator("zh")
        if not self._warmed:
            self._transcribe(
                np.zeros(16000, dtype=np.float32),
                language="de",
                vad_filter=False,
                beam_size=1,
                condition_on_previous_text=False,
            )
            self._warmed = True

    def _get_asr(self):
        if self.asr is None:
            path = self.store.require("whisper-small")
            if self.prefer_gpu:
                try:
                    prepare_cuda_dlls()
                    self.asr = self.asr_factory(path, device="cuda", compute_type="int8_float16")
                    self._using_gpu = True
                except (RuntimeError, OSError, ValueError) as exc:
                    self.warning = f"GPU 识别不可用，已切换 CPU（{type(exc).__name__}）"
            if self.asr is None:
                self.asr = self.asr_factory(path, device="cpu", compute_type="int8")
        return self.asr

    def _transcribe(self, audio: np.ndarray, **options):
        """Decode eagerly so GPU errors raised by lazy segments can use CPU once."""
        try:
            segments, info = self._get_asr().transcribe(audio, **options)
            return list(segments), info
        except (RuntimeError, OSError, ValueError) as exc:
            if not self._using_gpu:
                raise
            self._using_gpu = False
            self.prefer_gpu = False
            self.asr = None
            self.warning = f"GPU 识别运行失败，已切换 CPU（{type(exc).__name__}）"
            segments, info = self._get_asr().transcribe(audio, **options)
            return list(segments), info

    def _get_translator(self, language: str):
        if language not in self.translators:
            model_path = self.store.require(f"opus-{language}-de")
            self.translators[language] = OpusTranslator(model_path)
        return self.translators[language]

    @staticmethod
    def normalize_language_lock(language_lock: str | None) -> str | None:
        """``auto``/``None`` mean "trust the detector"; anything else must be known."""
        if language_lock in SUPPORTED_LANGUAGES:
            return language_lock
        return None

    def check_audio(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Everything the recognizer accepts, in one place, so both APIs agree."""
        if sample_rate != SAMPLE_RATE or audio.ndim != 1:
            raise ValueError("local engine needs 16 kHz mono audio")
        prepared = np.asarray(audio, dtype=np.float32)
        if not np.isfinite(prepared).all():
            raise ValueError("audio contains non-finite samples")
        return np.clip(prepared, -1.0, 1.0)

    def transcribe_segments(
        self,
        audio: np.ndarray,
        *,
        language: str | None = None,
        offset_samples: int | None = None,
        word_timestamps: bool = False,
        vad_filter: bool = True,
        beam_size: int = 3,
    ) -> TranscriptResult:
        """Pure transcription: recognized text with the model's own timings.

        This is the entry point the browser bridge and any file-to-subtitle
        path use. It performs no translation and no clock stamping, so a slow
        decode cannot influence the returned times. ``offset_samples`` is the
        position of ``audio`` inside a larger submitted stream and is folded
        into every ``start_sample``/``end_sample``, which is what lets a
        consumer place a caption on the video timeline.
        """
        prepared = self.check_audio(audio, SAMPLE_RATE)
        if offset_samples is not None and offset_samples < 0:
            raise ValueError("offset_samples must not be negative")
        if prepared.size == 0:
            return TranscriptResult(language=language or "")
        segments, info = self._transcribe(
            prepared,
            language=language,
            vad_filter=vad_filter,
            beam_size=beam_size,
            condition_on_previous_text=False,
            word_timestamps=word_timestamps,
            hallucination_silence_threshold=1.0 if word_timestamps else None,
        )
        detected = language or getattr(info, "language", "") or ""
        offset = offset_samples
        if offset is not None and offset < 0:
            raise ValueError("offset_samples must not be negative")
        converted: list[TranscriptSegment] = []
        for segment in segments:
            text = (getattr(segment, "text", "") or "").strip()
            if not text:
                continue
            start = _optional_number(getattr(segment, "start", 0.0))
            end = _optional_number(getattr(segment, "end", 0.0))
            if start is None or end is None or start < 0 or end < start or start > prepared.size / SAMPLE_RATE:
                continue
            start_ms = int(round(start * 1000))
            end_ms = int(round(min(end, prepared.size / SAMPLE_RATE) * 1000))
            converted.append(
                TranscriptSegment(
                    text=text,
                    language=detected,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    start_sample=(None if offset is None else offset + min(
                        prepared.size, max(0, round(start_ms * SAMPLE_RATE / 1000))
                    )),
                    end_sample=(None if offset is None else offset + min(
                        prepared.size, max(0, round(end_ms * SAMPLE_RATE / 1000))
                    )),
                    words=(
                        self._convert_words(segment, offset, start_ms, end_ms)
                        if word_timestamps
                        else ()
                    ),
                    no_speech_probability=_optional_number(
                        getattr(segment, "no_speech_prob", None)
                    ),
                    avg_logprob=_optional_number(getattr(segment, "avg_logprob", None)),
                )
            )
        return TranscriptResult(
            segments=tuple(converted),
            language=detected,
            language_probability=_optional_number(
                getattr(info, "language_probability", None)
            ),
            duration_seconds=_optional_number(getattr(info, "duration", None)),
            warning=self.warning,
            device="cuda" if self._using_gpu else "cpu",
        )

    def transcribe_audio(
        self, audio: np.ndarray, sample_rate: int = SAMPLE_RATE
    ) -> TranscriptResult:
        """Every word of ``audio``, with times relative to ``audio`` itself."""
        return self.transcribe_segments(
            self.check_audio(audio, sample_rate),
            offset_samples=0,
            word_timestamps=False,
        )

    @staticmethod
    def _convert_words(segment, offset: int | None, segment_start: int, segment_end: int) -> tuple[TranscriptWord, ...]:
        words: list[TranscriptWord] = []
        for word in getattr(segment, "words", None) or ():
            text = (getattr(word, "word", "") or "").strip()
            if not text:
                continue
            start = _optional_number(getattr(word, "start", None))
            end = _optional_number(getattr(word, "end", None))
            if start is None or end is None or start < 0 or end <= start:
                continue
            start_ms, end_ms = round(start * 1000), round(end * 1000)
            if start_ms < segment_start or end_ms > segment_end or (words and start_ms < words[-1].end_ms):
                continue
            words.append(
                TranscriptWord(
                    text=text,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    start_sample=(None if offset is None else offset + max(
                        0, round(start_ms * SAMPLE_RATE / 1000)
                    )),
                    end_sample=(None if offset is None else offset + max(
                        0, round(end_ms * SAMPLE_RATE / 1000)
                    )),
                    probability=_optional_number(getattr(word, "probability", None)),
                )
            )
        return tuple(words)

    def translate(self, text: str, language: str) -> TranscriptTranslation:
        """Translate one recognized segment into German.

        A failure is returned as data (``failed=True`` with the original text
        left alone) instead of raising: the caller must keep showing the
        original rather than dropping a caption it already recognized.
        """
        source = (text or "").strip()
        if not source:
            return TranscriptTranslation(language=language, text="", backend="none")
        if language == "de":
            return TranscriptTranslation(
                language=language, text=source, backend="identity"
            )
        if language not in ("en", "zh"):
            return TranscriptTranslation(
                language=language, text=source, backend="unsupported", failed=True
            )
        try:
            translated = self._get_translator(language).translate(source)
        except Exception as exc:  # model or runtime failure: keep the original
            self.warning = f"翻译失败，已保留原文（{type(exc).__name__}）"
            return TranscriptTranslation(
                language=language, text=source, backend=f"opus-{language}-de", failed=True
            )
        if not translated:
            return TranscriptTranslation(
                language=language, text=source, backend=f"opus-{language}-de", failed=True
            )
        return TranscriptTranslation(
            language=language, text=translated, backend=f"opus-{language}-de"
        )

    def process(
        self,
        audio: np.ndarray,
        sample_rate: int,
        session_id: int,
        segment_id: str,
        language_lock: str | None,
    ) -> CaptionEvent | None:
        if sample_rate != 16000 or audio.ndim != 1:
            raise ValueError("local engine needs 16 kHz mono audio")
        locked = self.normalize_language_lock(language_lock)
        result = self.transcribe_segments(
            audio,
            language=locked,
            offset_samples=None,
            word_timestamps=False,
        )
        original = result.text
        language = result.language
        if not original or language not in SUPPORTED_LANGUAGES:
            return None
        if language == "de":
            german = None
        else:
            translation = self.translate(original, language)
            if translation.failed or not translation.text:
                return None
            german = translation.text
        return CaptionEvent(session_id, segment_id, language, original, german, True, time.monotonic())
