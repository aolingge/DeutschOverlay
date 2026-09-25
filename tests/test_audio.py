from threading import Event

import numpy as np
import pytest

from deutsch_overlay.audio import (
    AudioDeviceError,
    AudioLevelMeter,
    LoopbackSource,
    SpeechSegmenter,
    list_output_devices,
)


def test_audio_level_meter_reports_signal_then_silence():
    meter = AudioLevelMeter(frames_per_update=3)
    silence = np.zeros(1600, dtype=np.float32)
    voice = np.full(1600, 0.2, dtype=np.float32)
    assert meter.push(silence) is None
    assert meter.push(voice) is None
    assert meter.push(silence) == pytest.approx(0.2)
    assert meter.push(silence) is None
    assert meter.push(silence) is None
    assert meter.push(silence) == 0.0


class FakeSpeaker:
    def __init__(self, name, id):
        self.name = name
        self.id = id


class FakeRecorder:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def record(self, numframes):
        if self.error:
            raise self.error
        return self.data[:numframes]


class FakeMic:
    def __init__(self, recorder):
        self.fake_recorder = recorder

    def recorder(self, **_kwargs):
        return self.fake_recorder


class FakeBackend:
    def __init__(self, recorder=None):
        self.speakers = [FakeSpeaker("Main", "one"), FakeSpeaker("Other", "two")]
        self.recorder = recorder or FakeRecorder(np.ones((1600, 2), dtype=np.float32))
        self.selected_id = None

    def all_speakers(self):
        return self.speakers

    def default_speaker(self):
        return self.speakers[0]

    def get_microphone(self, *, id, include_loopback):
        assert include_loopback
        self.selected_id = id
        return FakeMic(self.recorder)


def test_lists_output_devices_and_marks_default():
    devices = list_output_devices(FakeBackend())
    assert [(d.id, d.is_default) for d in devices] == [("one", True), ("two", False)]


def test_source_resolves_selected_device_and_converts_stereo_to_mono():
    backend = FakeBackend()
    source = LoopbackSource("two", backend=backend)
    frames = source.frames(Event())
    frame = next(frames)
    frames.close()
    assert backend.selected_id == "two"
    assert frame.shape == (1600,)
    assert frame.dtype == np.float32
    assert np.allclose(frame, 1)


def test_unknown_output_device_is_recoverable_error():
    with pytest.raises(AudioDeviceError, match="unavailable"):
        next(LoopbackSource("gone", backend=FakeBackend()).frames(Event()))


def test_recorder_failure_is_wrapped_without_returning_stale_audio():
    backend = FakeBackend(FakeRecorder(error=OSError("device disconnected")))
    with pytest.raises(AudioDeviceError, match="capture failed"):
        next(LoopbackSource(backend=backend).frames(Event()))


def test_segmenter_ignores_silence_and_short_noise():
    segmenter = SpeechSegmenter(sample_rate=16000, frame_samples=1600)
    silence = np.zeros(1600, dtype=np.float32)
    voice = np.full(1600, 0.15, dtype=np.float32)
    assert segmenter.push(silence) == []
    assert segmenter.push(voice) == []
    for _ in range(6):
        assert segmenter.push(silence) == []
    assert segmenter.flush() is None

def test_segmenter_emits_speech_after_trailing_silence():
    segmenter = SpeechSegmenter(sample_rate=16000, frame_samples=1600)
    voice = np.full(1600, 0.15, dtype=np.float32)
    silence = np.zeros(1600, dtype=np.float32)
    for _ in range(3):
        segmenter.push(voice)
    outputs = []
    for _ in range(5):
        outputs.extend(segmenter.push(silence))
    assert len(outputs) == 1
    assert outputs[0].dtype == np.float32
    assert len(outputs[0]) >= 3 * 1600


def test_shorter_trailing_silence_emits_caption_clip_earlier():
    segmenter = SpeechSegmenter(sample_rate=16000, frame_samples=1600, silence_seconds=0.3)
    voice = np.full(1600, 0.15, dtype=np.float32)
    silence = np.zeros(1600, dtype=np.float32)
    for _ in range(3):
        assert segmenter.push(voice) == []
    assert segmenter.push(silence) == []
    assert segmenter.push(silence) == []
    assert len(segmenter.push(silence)) == 1


def test_segmenter_bounds_long_speech():
    segmenter = SpeechSegmenter(sample_rate=16000, frame_samples=1600, max_seconds=0.5)
    voice = np.full(1600, 0.15, dtype=np.float32)
    outputs = []
    for _ in range(8):
        outputs.extend(segmenter.push(voice))
    assert outputs
    assert len(outputs[0]) <= 16000 // 2


def test_segmenter_snapshot_keeps_current_speech_for_final_caption():
    segmenter = SpeechSegmenter(sample_rate=16000, frame_samples=1600, max_seconds=5)
    voice = np.full(1600, 0.15, dtype=np.float32)
    for _ in range(20):
        assert segmenter.push(voice) == []
    preview = segmenter.snapshot()
    assert preview is not None and len(preview) == 20 * 1600
    assert len(segmenter.flush()) == 20 * 1600


def test_segmenter_accepts_quiet_playback_speech():
    segmenter = SpeechSegmenter(sample_rate=16000, frame_samples=1600)
    voice = np.full(1600, 0.008, dtype=np.float32)
    silence = np.zeros(1600, dtype=np.float32)
    outputs = []
    for frame in [voice] * 4 + [silence] * 5:
        outputs.extend(segmenter.push(frame))
    assert len(outputs) == 1


def test_silero_gate_ignores_continuous_music_like_tone():
    pytest.importorskip("faster_whisper")
    from deutsch_overlay.audio import SileroSpeechDetector

    detector = SileroSpeechDetector()
    segmenter = SpeechSegmenter(sample_rate=16000, frame_samples=1600, voice_detector=detector)
    t = np.arange(1600, dtype=np.float32) / 16000
    tone = 0.014 * np.sin(2 * np.pi * 440 * t)
    outputs = []
    for _ in range(80):
        outputs.extend(segmenter.push(tone))
    assert outputs == []
    assert segmenter.flush() is None
