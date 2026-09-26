"""Offline speech recognition and German translation."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from deutsch_overlay.captions import CaptionEvent
from deutsch_overlay.gpu_runtime import prepare_cuda_dlls
from deutsch_overlay.models import ModelStore


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
        locked = language_lock if language_lock in {"de", "en", "zh"} else None
        segments, info = self._transcribe(
            np.asarray(audio, dtype=np.float32),
            language=locked,
            vad_filter=True,
            beam_size=3,
            condition_on_previous_text=False,
        )
        original = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
        language = locked or getattr(info, "language", "")
        if not original or language not in {"de", "en", "zh"}:
            return None
        german = None
        if language != "de":
            german = self._get_translator(language).translate(original)
            if not german:
                return None
        return CaptionEvent(session_id, segment_id, language, original, german, True, time.monotonic())
