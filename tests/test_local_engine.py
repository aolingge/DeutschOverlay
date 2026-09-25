from dataclasses import dataclass

import numpy as np
import pytest

from deutsch_overlay.engines.local import LocalEngine
from deutsch_overlay.models import MissingModelError, ModelStore


@dataclass
class FakeSegment:
    text: str


@dataclass
class FakeInfo:
    language: str


class FakeAsr:
    def __init__(self, language, text):
        self.language = language
        self.text = text
        self.requested_language = None

    def transcribe(self, _audio, **kwargs):
        self.requested_language = kwargs["language"]
        return [FakeSegment(self.text)], FakeInfo(self.language)


class FakeTranslator:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def translate(self, text):
        self.calls.append(text)
        return self.result


def run_engine(asr, translators=None, language_lock=None):
    engine = LocalEngine(asr=asr, translators=translators or {})
    return engine.process(np.ones(16000, dtype=np.float32), 16000, 4, "clip-1", language_lock)


def test_german_is_transcribed_without_translation():
    translator = FakeTranslator("wrong")
    result = run_engine(FakeAsr("de", "  Guten Tag  "), {"de": translator})
    assert (result.language, result.original, result.german) == ("de", "Guten Tag", None)
    assert translator.calls == []


@pytest.mark.parametrize(
    "language,original,german",
    [("en", "Hello", "Hallo"), ("zh", "你好", "Hallo")],
)
def test_foreign_language_is_translated_to_german(language, original, german):
    translator = FakeTranslator(german)
    result = run_engine(FakeAsr(language, original), {language: translator})
    assert (result.language, result.original, result.german) == (language, original, german)
    assert translator.calls == [original]


def test_manual_language_lock_overrides_detector():
    asr = FakeAsr("en", "Guten Tag")
    result = run_engine(asr, language_lock="de")
    assert asr.requested_language == "de"
    assert result.language == "de"


def test_empty_or_unsupported_speech_is_not_displayed():
    assert run_engine(FakeAsr("de", "   ")) is None
    assert run_engine(FakeAsr("fr", "Bonjour")) is None


def test_model_store_reports_missing_model_without_downloading(tmp_path):
    store = ModelStore(tmp_path)
    with pytest.raises(MissingModelError, match="whisper-small"):
        store.require("whisper-small")
    assert list(tmp_path.iterdir()) == []


def test_gpu_initialization_falls_back_to_cpu_with_warning(tmp_path):
    class FakeStore:
        def require(self, _name):
            return tmp_path

    calls = []

    def asr_factory(_path, *, device, compute_type):
        calls.append((device, compute_type))
        if device == "cuda":
            raise RuntimeError("cuDNN unavailable")
        return FakeAsr("de", "Hallo")

    engine = LocalEngine(model_store=FakeStore(), asr_factory=asr_factory)
    result = engine.process(np.ones(16000, dtype=np.float32), 16000, 1, "a", None)
    assert result.original == "Hallo"
    assert calls[0][0] == "cuda"
    assert calls[1][0] == "cpu"
    assert "CPU" in engine.warning


def test_rejects_wrong_audio_format():
    with pytest.raises(ValueError, match="16 kHz mono"):
        LocalEngine(asr=FakeAsr("de", "Hallo")).process(
            np.ones((2, 2), dtype=np.float32), 44100, 1, "a", None
        )


def test_prepare_loads_recognition_and_both_translation_models(tmp_path):
    class Store:
        def require(self, name):
            return tmp_path / name

    loaded = []
    engine = LocalEngine(
        model_store=Store(),
        asr_factory=lambda path, **_kwargs: loaded.append(path.name) or FakeAsr("de", "Hallo"),
        prefer_gpu=False,
    )
    engine._get_translator = lambda code: loaded.append(code)
    engine.prepare()
    assert loaded == ["whisper-small", "en", "zh"]


def test_prepare_decodes_once_so_first_live_caption_is_not_cold():
    class CountingAsr:
        def __init__(self):
            self.decodes = 0

        def transcribe(self, audio, **kwargs):
            assert audio.shape == (16000,)
            assert kwargs["vad_filter"] is False

            def segments():
                self.decodes += 1
                yield FakeSegment("")

            return segments(), FakeInfo("de")

    asr = CountingAsr()
    engine = LocalEngine(asr=asr, translators={"en": FakeTranslator(""), "zh": FakeTranslator("")})
    engine.prepare()
    engine.prepare()
    assert asr.decodes == 1
