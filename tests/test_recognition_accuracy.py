"""Signal/timeline regressions; these do not measure speech WER."""
from types import SimpleNamespace

import numpy as np
import pytest

from deutsch_overlay.audio import PositionedSpeechSegmenter, SpeechSpan
from deutsch_overlay.browser_bridge import AsrBridgeService, BridgeSession, ClipJob
from deutsch_overlay.browser_protocol import SessionRequest
from deutsch_overlay.engines.local import LocalEngine
from deutsch_overlay.pcm import Resampler
from deutsch_overlay.transcript import TranscriptResult, TranscriptSegment, TranscriptWord
from deutsch_overlay.browser_protocol import AudioRequest, AudioPacket
from deutsch_overlay.pcm import TimelineAnchor, decode_packet
import base64


@pytest.mark.parametrize("rate", [32000, 44100, 48000])
def test_resampling_preserves_speech_band_and_rejects_aliasing(rate):
    for frequency in (440, 3000, 10000):
        source = np.sin(2 * np.pi * frequency * np.arange(rate) / rate).astype(np.float32)
        converter = Resampler(rate)
        output = np.concatenate((converter.process(source), converter.flush()))
        interior = output[500:-500]
        rms = np.sqrt(np.mean(interior ** 2))
        if frequency < 8000:
            reference = np.sin(2 * np.pi * frequency * np.arange(len(output)) / 16000)[500:-500]
            snr = 10 * np.log10(np.mean(reference ** 2) / np.mean((interior - reference) ** 2))
            assert snr > 50
            assert rms == pytest.approx(2 ** -0.5, abs=0.01)
        else:
            assert rms < 0.003


def test_resampler_rejects_nonfinite_audio_and_requires_reset_after_flush():
    converter = Resampler(48000)
    with pytest.raises(ValueError, match="non-finite"):
        converter.process(np.array([np.nan]))
    converter.process(np.ones(4800, dtype=np.float32))
    converter.flush()
    assert converter.flush().size == 0
    with pytest.raises(ValueError, match="reset"):
        converter.process(np.ones(4800))
    converter.reset()
    assert converter.process(np.ones(4800)).size > 0


def test_snapshot_and_final_span_keep_preroll_position_after_silence():
    segmenter = PositionedSpeechSegmenter(sample_rate=16000, frame_samples=1600)
    for _ in range(10):
        segmenter.push_positioned(np.zeros(1600, dtype=np.float32))
    for _ in range(3):
        segmenter.push_positioned(np.ones(1600, dtype=np.float32) * 0.1)
    assert segmenter.utterance_start_sample == 12800
    snapshot = segmenter.snapshot()
    final = segmenter.flush_positioned()
    assert final.start_sample == 12800
    assert final.end_sample == 20800
    assert np.array_equal(snapshot, final.samples)


class Detector:
    def __init__(self):
        self.calls = []
        self.probability = 0.2
        self.language = "en"

    def transcribe_segments(self, _audio, **options):
        self.calls.append(options)
        return TranscriptResult(language=self.language, language_probability=self.probability,
                                segments=(TranscriptSegment(text="Hallo", language=self.language,
                                                            start_ms=0, end_ms=1000),))


def test_auto_language_requires_two_independent_confident_final_utterances():
    detector = Detector()
    service = AsrBridgeService(detector)
    request = SessionRequest(platform="youtube", video_key="testvideo123", caption_availability="absent")
    session = BridgeSession("example", request, True, "absent", "fake")
    service._publish = lambda *_args, **_kwargs: None
    def run(start, provisional=False):
        service._run_job(session, ClipJob(SpeechSpan(np.ones(32000), start, start + 32000), provisional, "auto"))
    run(0)
    assert not session.language_confirmed
    detector.probability = 0.99
    detector.language = "de"
    run(32000, provisional=True)
    run(32000)
    run(32000)
    assert not session.language_confirmed
    run(64000)
    assert session.language_confirmed and session.language == "de"
    run(96000)
    assert detector.calls[-1]["language"] == "de"
    assert detector.calls[-1]["word_timestamps"] is True
    assert detector.calls[-1]["beam_size"] == 3
    service._reset_audio_state(session)
    assert not session.language_confirmed
    assert session.language == "auto"


def test_stale_decode_cannot_change_language_evidence():
    detector = Detector()
    detector.probability = 0.99
    service = AsrBridgeService(detector)
    request = SessionRequest(platform="youtube", video_key="testvideo123", caption_availability="absent")
    session = BridgeSession("example", request, True, "absent", "fake", timeline_epoch=1)
    service._run_job(session, ClipJob(SpeechSpan(np.ones(32000), 0, 32000), False, "auto", epoch=0))
    assert not session.language_evidence
    assert not session.revisions


def test_local_engine_rejects_invalid_timings_and_bounds_words_to_audio():
    words = [SimpleNamespace(word="good", start=0.1, end=0.3, probability=0.9),
             SimpleNamespace(word="bad", start=float("nan"), end=0.6),
             SimpleNamespace(word="outside", start=1.0, end=3.0)]
    class Asr:
        def transcribe(self, audio, **options):
            assert options["hallucination_silence_threshold"] == 1.0
            return [SimpleNamespace(text="good", start=0.0, end=2.0, words=words),
                    SimpleNamespace(text="bad", start=float("nan"), end=1.0)], SimpleNamespace(language="en")
    engine = LocalEngine(asr=Asr())
    result = engine.transcribe_segments(np.ones(16000), offset_samples=32000, word_timestamps=True)
    assert len(result.segments) == 1
    assert result.segments[0].end_ms == 1000
    assert result.segments[0].end_sample == 48000
    assert len(result.segments[0].words) == 1
    assert result.segments[0].words[0].start_sample == 33600
    with pytest.raises(ValueError, match="non-finite"):
        engine.transcribe_segments(np.array([np.inf]))


def test_final_audio_keeps_filter_state_and_reports_real_tail_without_padding():
    service = AsrBridgeService(Detector(), frame_samples=1600)
    service._wake = lambda *_: None
    request = SessionRequest(platform="youtube", video_key="testvideo123", caption_availability="absent")
    session = BridgeSession("example", request, True, "absent", "fake")
    converter = None
    length = 44100 * 2 + 137
    source = np.full(length, 0.1, dtype=np.float32)
    for i, start in enumerate(range(0, length, 4410)):
        pcm = base64.b64encode((source[start:start + 4410] * 32767).astype("<i2").tobytes()).decode()
        packet = AudioPacket(i, start, pcm, sample_rate=44100)
        service.feed(session, AudioRequest((packet,)))
        if converter is None:
            converter = session.resampler
        assert session.resampler is converter
    response = service.feed(session, AudioRequest((), stream_complete=True))
    expected = int(np.ceil(length * 16000 / 44100))
    assert response.accepted_range[1] == session.next_sample == expected
    assert response.accepted_samples == expected
    assert session.carry.size == 0
    assert session.jobs
    assert session.jobs[-1].span.end_sample == expected
    assert session.jobs[-1].span.samples.size == expected


def test_small_audio_hole_is_before_the_later_packet():
    service = AsrBridgeService(Detector())
    service._wake = lambda *_: None
    request = SessionRequest(platform="youtube", video_key="testvideo123", caption_availability="absent")
    session = BridgeSession("example", request, True, "absent", "fake")
    observed = []
    service._push_audio = lambda _session, audio: observed.append(audio.copy())
    payload = base64.b64encode((np.ones(1600) * 1000).astype("<i2").tobytes()).decode()
    service.feed(session, AudioRequest((AudioPacket(0, 0, payload), AudioPacket(1, 1760, payload))))
    assert np.array_equal(observed[1][:160], np.zeros(160))
    assert np.all(observed[1][160:] > 0)


def test_word_coordinates_follow_video_anchor_and_survive_translation_revision():
    service = AsrBridgeService(Detector())
    request = SessionRequest(platform="youtube", video_key="testvideo123", caption_availability="absent")
    session = BridgeSession("example", request, True, "absent", "fake")
    session.timeline.add_anchor(TimelineAnchor(0, 120000, 0))
    result = TranscriptResult(language="de", segments=(TranscriptSegment(
        text="Hallo", language="de", start_ms=100, end_ms=500,
        start_sample=33600, end_sample=40000,
        words=(TranscriptWord("Hallo", 100, 500, 33600, 40000, 0.9),)),))
    service._publish(session, result, provisional=True)
    view = session.all_segments()[0]
    assert view.start_ms == view.words[0]["startMs"] == 122100
    assert view.end_ms == view.words[0]["endMs"] == 122500


def test_seek_reset_drops_preroll_and_detector_context():
    class VoiceDetector:
        resets = 0
        def __call__(self, frame):
            return bool(np.max(frame) > 0.05)
        def reset(self):
            self.resets += 1
    detector = VoiceDetector()
    segmenter = PositionedSpeechSegmenter(sample_rate=16000, frame_samples=1600, voice_detector=detector)
    segmenter.push_positioned(np.ones(1600, dtype=np.float32) * 0.01)
    segmenter.reset()
    assert detector.resets == 1
    for _ in range(3):
        segmenter.push_positioned(np.ones(1600, dtype=np.float32) * 0.1)
    span = segmenter.flush_positioned()
    assert span.start_sample == 0 and span.end_sample == 4800
    assert np.all(span.samples > 0.05)
