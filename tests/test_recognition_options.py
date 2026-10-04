"""Recognition configuration, preserved evidence and non-speech rejection."""
from types import SimpleNamespace

import numpy as np
import pytest

from deutsch_overlay.browser_cli import build_parser, build_speech_segmenter, main
from deutsch_overlay.engines.local import LocalEngine
from deutsch_overlay.transcript import TranscriptSegment
from deutsch_overlay.transcript import TranscriptResult, TranscriptTranslation
from deutsch_overlay.browser_bridge import AsrBridgeService, BridgeSession
from deutsch_overlay.browser_protocol import SessionRequest


class Asr:
    def __init__(self, text="這是軟體。", language="zh", words=()):
        self.text, self.language, self.words = text, language, words
        self.options = []

    def transcribe(self, _audio, **options):
        self.options.append(options)
        return [SimpleNamespace(text=self.text, start=0, end=1, words=self.words)], SimpleNamespace(language=self.language)


def test_script_conversion_preserves_raw_evidence_and_word_positions():
    pytest.importorskip("opencc")
    asr = Asr(words=[SimpleNamespace(word="這是", start=0.1, end=0.4, probability=.9),
                    SimpleNamespace(word="軟體。", start=.4, end=.9, probability=.8)])
    result = LocalEngine(asr=asr, chinese_script="simplified").transcribe_segments(
        np.ones(16000), word_timestamps=True, offset_samples=32000)
    segment = result.segments[0]
    assert segment.text == "这是软体。"
    assert segment.raw_text == "這是軟體。"
    assert [(w.text, w.start_sample, w.end_sample) for w in segment.words] == [
        ("这是", 33600, 38400), ("软体。", 38400, 46400)]
    assert TranscriptSegment.from_dict(segment.to_dict()) == segment


def test_phrase_conversion_drops_ambiguous_word_mapping():
    # A phrase dictionary may change across token boundaries. Never invent
    # word positions when individually normalized words no longer match.
    asr = Asr(text="AB", words=[SimpleNamespace(word="A", start=.1, end=.4),
                              SimpleNamespace(word="B", start=.4, end=.8)])
    engine = LocalEngine(asr=asr, chinese_script="simplified")
    engine._chinese_converter = SimpleNamespace(convert=lambda s: {"AB": "XY", "A": "X", "B": "Z"}[s])
    segment = engine.transcribe_segments(np.ones(16000), word_timestamps=True).segments[0]
    assert segment.text == "XY" and segment.raw_text == "AB" and segment.words == ()


def test_non_chinese_and_raw_mode_keep_model_output():
    for language, script in (("en", "simplified"), ("zh", "raw")):
        segment = LocalEngine(asr=Asr(language=language), chinese_script=script).transcribe_segments(np.ones(16000)).segments[0]
        assert segment.text == segment.raw_text == "這是軟體。"


def test_translation_revision_keeps_raw_and_display_source():
    engine = SimpleNamespace(translate=lambda *_: TranscriptTranslation(language="zh", text="Hallo", backend="fake"))
    service = AsrBridgeService(engine)
    request = SessionRequest(platform="youtube", video_key="testvideo123", caption_availability="absent")
    session = BridgeSession("example", request, True, "absent", "fake")
    result = TranscriptResult(language="zh", segments=(TranscriptSegment(text="这是", raw_text="這是", language="zh",
                              start_ms=0, end_ms=1000, start_sample=0, end_sample=16000),))
    service._publish(session, result, provisional=False)
    service._start_worker(session)
    assert session.idle.wait(3)
    service._stop_worker(session)
    view = session.all_segments()[0]
    assert view.original == "这是" and view.raw_original == "這是" and view.german == "Hallo"
    assert view.to_dict()["rawOriginal"] == "這是"


def test_terms_are_only_sent_for_the_requested_language():
    asr = Asr(language="en")
    engine = LocalEngine(asr=asr, hotwords={"de": "CTranslate2", "en": "PyAV"})
    for language, expected in ((None, None), ("de", "CTranslate2"), ("en", "PyAV"), ("zh", None)):
        engine.transcribe_segments(np.ones(16000), language=language)
        assert asr.options[-1]["hotwords"] == expected
        assert asr.options[-1]["condition_on_previous_text"] is False


@pytest.mark.parametrize("terms", [{"xx": "word"}, {"en": 42}, {"en": "x" * 1001}, {"en": "x\ny"}])
def test_invalid_terms_rejected(terms):
    with pytest.raises(ValueError, match="术语表"):
        LocalEngine(hotwords=terms)


def test_selected_local_model_is_reused_during_gpu_fallback(tmp_path):
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (tmp_path / name).write_text("fake")
    calls = []
    def factory(path, **options):
        calls.append((path, options["device"]))
        if options["device"] == "cuda":
            raise RuntimeError("fake GPU failure")
        return Asr(language="de")
    engine = LocalEngine(asr_model_path=tmp_path, asr_factory=factory)
    engine.transcribe_segments(np.ones(16000))
    assert calls == [(tmp_path, "cuda"), (tmp_path, "cpu")]
    with pytest.raises(ValueError, match="不会自动下载"):
        LocalEngine(asr_model_path=tmp_path / "missing")


def test_cli_defaults_and_private_config_error(tmp_path, capsys):
    args = build_parser().parse_args([])
    assert args.chinese_script == "simplified" and args.speech_gate == "silero"
    private = tmp_path / "terms.json"
    private.write_text('{"en": "PRIVATE TERM",', encoding="utf-8")
    assert main(["--hotwords-file", str(private)]) == 2
    captured = capsys.readouterr()
    assert "配置无效" in captured.err and "PRIVATE TERM" not in captured.err


def test_actual_cli_segmenter_rejects_tone_and_silence_without_playback():
    pytest.importorskip("faster_whisper")
    segmenter = build_speech_segmenter(sample_rate=16000, frame_samples=1600)
    tone = (.1 * np.sin(2 * np.pi * 440 * np.arange(1600) / 16000)).astype(np.float32)
    spans = [span for _ in range(80) for span in segmenter.push_positioned(tone)]
    assert not spans and segmenter.flush_positioned() is None
    segmenter.reset()
    assert all(not segmenter.push_positioned(np.zeros(1600, np.float32)) for _ in range(20))
