from threading import Event, Thread, Timer
import time

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


def test_loopback_preserves_phase_inverted_stereo_speech():
    stereo = np.column_stack((
        np.full(1600, 0.2, dtype=np.float32),
        np.full(1600, -0.2, dtype=np.float32),
    ))
    frames = LoopbackSource(backend=FakeBackend(FakeRecorder(stereo))).frames(Event())
    try:
        assert np.max(np.abs(next(frames))) == pytest.approx(0.2)
    finally:
        frames.close()


def test_loopback_preserves_center_only_surround_speech():
    surround = np.zeros((1600, 6), dtype=np.float32)
    surround[:, 2] = 0.12
    frames = LoopbackSource(backend=FakeBackend(FakeRecorder(surround))).frames(Event())
    try:
        assert np.max(np.abs(next(frames))) == pytest.approx(0.12)
    finally:
        frames.close()


def test_surround_downmix_keeps_quiet_center_speech_under_loud_background():
    surround = np.full((1600, 6), 0.2, dtype=np.float32)
    surround[:, 2] = np.tile(np.array([0.01, -0.01], dtype=np.float32), 800)
    frames = LoopbackSource(backend=FakeBackend(FakeRecorder(surround))).frames(Event())
    try:
        mono = next(frames)
        assert float(np.std(mono)) > 0.001
    finally:
        frames.close()


def test_surround_downmix_does_not_dilute_center_voice_with_noise_floor():
    surround = np.full((1600, 6), 0.00001, dtype=np.float32)
    surround[:, 2] = np.tile(np.array([0.012, -0.012], dtype=np.float32), 800)
    frames = LoopbackSource(backend=FakeBackend(FakeRecorder(surround))).frames(Event())
    try:
        assert float(np.max(np.abs(next(frames)))) > 0.003
    finally:
        frames.close()


def test_default_loopback_follows_output_device_change():
    class SwitchingBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.default_id = "one"
            self.opened = []

        def default_speaker(self):
            return next(speaker for speaker in self.speakers if speaker.id == self.default_id)

        def get_microphone(self, *, id, include_loopback):
            assert include_loopback
            self.opened.append(id)
            backend = self

            class SwitchingRecorder(FakeRecorder):
                def record(self, numframes):
                    if id == "one":
                        backend.default_id = "two"
                    value = 1 if id == "one" else 2
                    return np.full((numframes, 2), value, dtype=np.float32)

            return FakeMic(SwitchingRecorder())

    backend = SwitchingBackend()
    source = LoopbackSource(backend=backend, device_check_frames=2)
    frames = source.frames(Event())
    try:
        values = [float(next(frames)[0]) for _ in range(3)]
    finally:
        frames.close()
    assert values == [1, 1, 2]
    assert backend.opened == ["one", "two"]
    assert source.active_device.id == "two"


def test_explicit_output_device_does_not_follow_default_change():
    class ChangedDefaultBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.opened = []

        def default_speaker(self):
            return self.speakers[0] if not self.opened else self.speakers[1]

        def get_microphone(self, *, id, include_loopback):
            assert include_loopback
            self.opened.append(id)
            value = 1 if id == "one" else 2
            return FakeMic(FakeRecorder(np.full((1600, 2), value, dtype=np.float32)))

    backend = ChangedDefaultBackend()
    source = LoopbackSource("one", backend=backend, device_check_frames=1)
    frames = source.frames(Event())
    try:
        assert [float(next(frames)[0]) for _ in range(3)] == [1, 1, 1]
        assert backend.opened == ["one"]
    finally:
        frames.close()


def test_default_loopback_reopens_when_previous_device_disconnects():
    class DisconnectedBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.default_id = "one"
            self.opened = []

        def default_speaker(self):
            return next(speaker for speaker in self.speakers if speaker.id == self.default_id)

        def get_microphone(self, *, id, include_loopback):
            assert include_loopback
            self.opened.append(id)
            backend = self

            class DisconnectingRecorder(FakeRecorder):
                def record(self, numframes):
                    if id == "one":
                        backend.default_id = "two"
                        raise OSError("headset unplugged")
                    return np.full((numframes, 2), 2, dtype=np.float32)

            return FakeMic(DisconnectingRecorder())

    backend = DisconnectedBackend()
    frames = LoopbackSource(backend=backend).frames(Event())
    try:
        assert float(next(frames)[0]) == 2
    finally:
        frames.close()
    assert backend.opened == ["one", "two"]


def test_default_loopback_retries_before_default_switch_is_reported():
    class LateSwitchBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.default_calls = 0
            self.opened = []

        def default_speaker(self):
            self.default_calls += 1
            return self.speakers[0] if self.default_calls <= 2 else self.speakers[1]

        def get_microphone(self, *, id, include_loopback):
            assert include_loopback
            self.opened.append(id)
            if id == "one":
                return FakeMic(FakeRecorder(error=OSError("old stream disconnected")))
            return FakeMic(FakeRecorder(np.full((1600, 2), 2, dtype=np.float32)))

    backend = LateSwitchBackend()
    frames = LoopbackSource(backend=backend).frames(Event())
    try:
        assert float(next(frames)[0]) == 2
    finally:
        frames.close()
    assert backend.opened == ["one", "one", "two"]


def test_default_loopback_retries_same_device_after_stream_restart():
    class RestartedBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.opened = 0

        def get_microphone(self, *, id, include_loopback):
            assert id == "one" and include_loopback
            self.opened += 1
            if self.opened == 1:
                return FakeMic(FakeRecorder(error=OSError("stream restarted")))
            return FakeMic(FakeRecorder(np.ones((1600, 2), dtype=np.float32)))

    backend = RestartedBackend()
    source = LoopbackSource(backend=backend)
    frames = source.frames(Event())
    try:
        assert float(next(frames)[0]) == 1
    finally:
        frames.close()
    assert backend.opened == 2
    assert source.capture_generation == 2


def test_default_loopback_reports_persistent_capture_failure():
    backend = FakeBackend(FakeRecorder(error=OSError("device broken")))
    source = LoopbackSource(backend=backend)
    source.RECONNECT_SECONDS = 0.05
    with pytest.raises(AudioDeviceError, match="capture failed"):
        next(source.frames(Event()))


def test_recovering_loopback_waits_for_default_device_and_reports_state():
    class LaterBackend(FakeBackend):
        probes = 0

        def default_speaker(self):
            self.probes += 1
            return None if self.probes < 7 else self.speakers[0]

        def all_speakers(self):
            return [] if self.probes < 7 else self.speakers

    statuses = []
    source = LoopbackSource(
        backend=LaterBackend(), retry_forever=True, retry_interval=0.01,
        on_status=statuses.append,
    )
    source.RECONNECT_SECONDS = 0.02
    frames = source.frames(Event())
    try:
        assert float(next(frames)[0]) == 1
    finally:
        frames.close()
    assert len(statuses) == 1
    assert "等待" in statuses[0]


def test_recovering_loopback_keeps_manually_selected_device():
    class LaterBackend(FakeBackend):
        probes = 0

        def default_speaker(self):
            self.probes += 1
            return self.speakers[1]

        def all_speakers(self):
            return [] if self.probes < 4 else self.speakers

    backend = LaterBackend()
    source = LoopbackSource("one", backend=backend, retry_forever=True, retry_interval=0.01)
    frames = source.frames(Event())
    try:
        assert float(next(frames)[0]) == 1
    finally:
        frames.close()
    assert backend.selected_id == "one"


def test_recovering_loopback_can_stop_while_no_device_exists():
    class MissingBackend(FakeBackend):
        def default_speaker(self):
            return None

        def all_speakers(self):
            return []

    stop = Event()
    source = LoopbackSource(backend=MissingBackend(), retry_forever=True, retry_interval=0.5)
    finished = Event()

    def consume():
        assert list(source.frames(stop)) == []
        finished.set()

    worker = Thread(target=consume)
    worker.start()
    time.sleep(0.03)
    stop.set()
    worker.join(timeout=0.5)
    assert finished.is_set()


def test_recovering_loopback_does_not_retry_invalid_audio_format():
    class BadFormatRecorder(FakeRecorder):
        calls = 0

        def record(self, numframes):
            self.calls += 1
            return np.ones((numframes, 2, 2), dtype=np.float32)

    recorder = BadFormatRecorder()
    source = LoopbackSource(
        backend=FakeBackend(recorder),
        retry_forever=True, retry_interval=0.01,
    )
    with pytest.raises(AudioDeviceError, match="unsupported audio format"):
        next(source.frames(Event()))
    assert recorder.calls == 1


def test_recovering_loopback_reports_backend_unsupported_format_instead_of_looping():
    recorder = FakeRecorder(error=RuntimeError("unsupported format"))
    source = LoopbackSource(backend=FakeBackend(recorder), retry_forever=True, retry_interval=0.001)
    stop = Event()
    timer = Timer(0.05, stop.set)
    timer.start()
    try:
        with pytest.raises(AudioDeviceError, match="unsupported format"):
            next(source.frames(stop))
    finally:
        stop.set()
        timer.join()


def test_default_loopback_waits_for_temporary_missing_default():
    class BrieflyMissingBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.default_calls = 0
            self.opened = []

        def default_speaker(self):
            self.default_calls += 1
            if self.default_calls in {2, 3}:
                return None
            return self.speakers[0] if self.default_calls == 1 else self.speakers[1]

        def get_microphone(self, *, id, include_loopback):
            assert include_loopback
            self.opened.append(id)
            value = 1 if id == "one" else 2
            return FakeMic(FakeRecorder(np.full((1600, 2), value, dtype=np.float32)))

    backend = BrieflyMissingBackend()
    frames = LoopbackSource(backend=backend, device_check_frames=1).frames(Event())
    try:
        assert [float(next(frames)[0]) for _ in range(2)] == [1, 2]
    finally:
        frames.close()
    assert backend.opened == ["one", "two"]


def test_default_loopback_survives_temporary_device_listing_error():
    class BrieflyFailingBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.default_calls = 0
            self.opened = []

        def default_speaker(self):
            self.default_calls += 1
            if self.default_calls == 2:
                raise RuntimeError("device list changing")
            return self.speakers[0] if self.default_calls == 1 else self.speakers[1]

        def get_microphone(self, *, id, include_loopback):
            assert include_loopback
            self.opened.append(id)
            value = 1 if id == "one" else 2
            return FakeMic(FakeRecorder(np.full((1600, 2), value, dtype=np.float32)))

    backend = BrieflyFailingBackend()
    frames = LoopbackSource(backend=backend, device_check_frames=1).frames(Event())
    try:
        assert [float(next(frames)[0]) for _ in range(2)] == [1, 2]
    finally:
        frames.close()
    assert backend.opened == ["one", "two"]


def test_unknown_output_device_is_recoverable_error():
    with pytest.raises(AudioDeviceError, match="unavailable"):
        next(LoopbackSource("gone", backend=FakeBackend()).frames(Event()))


def test_recorder_failure_is_wrapped_without_returning_stale_audio():
    backend = FakeBackend(FakeRecorder(error=OSError("device disconnected")))
    with pytest.raises(AudioDeviceError, match="capture failed"):
        next(LoopbackSource("one", backend=backend).frames(Event()))


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


def test_segmenter_preserves_context_across_forced_long_speech_cut():
    segmenter = SpeechSegmenter(sample_rate=16000, frame_samples=1600, max_seconds=7)
    clips = []
    for index in range(80):
        frame = np.full(1600, 0.1 + index / 1000, dtype=np.float32)
        clips.extend(segmenter.push(frame))
    remainder = segmenter.flush()
    if remainder is not None:
        clips.append(remainder)
    assert len(clips) == 2
    assert len(clips[0]) == 7 * 16000
    assert len(clips[1]) <= 7 * 16000
    assert np.array_equal(clips[0][-4 * 1600:], clips[1][:4 * 1600])


def test_forced_cut_does_not_emit_overlap_for_one_new_voice_frame():
    segmenter = SpeechSegmenter(sample_rate=16000, frame_samples=1600, max_seconds=7,
                                silence_seconds=0.3)
    voice = np.full(1600, 0.15, dtype=np.float32)
    silence = np.zeros(1600, dtype=np.float32)
    clips = [clip for _ in range(70) for clip in segmenter.push(voice)]
    assert len(clips) == 1
    assert segmenter.push(voice) == []
    for _ in range(3):
        clips.extend(segmenter.push(silence))
    assert len(clips) == 1


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
