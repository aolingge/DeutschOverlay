import os
import time
import threading
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from deutsch_overlay.audio import LoopbackSource, OutputDevice
from deutsch_overlay.captions import CaptionEvent
from deutsch_overlay.config import Settings
from deutsch_overlay.controller import CaptionController
from deutsch_overlay.engines.azure import OnlineUnavailable


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FiniteSource:
    def __init__(self, frames):
        self._frames = frames

    def frames(self, _stop):
        yield from self._frames


class FakeLocalEngine:
    warning = None

    def process(self, _audio, _rate, session, segment, _lock):
        return CaptionEvent(session, segment, "de", "Guten Tag", None, True, time.monotonic())


def speech_frames():
    voice = np.full(1600, 0.2, dtype=np.float32)
    silence = np.zeros(1600, dtype=np.float32)
    return [voice] * 3 + [silence] * 5


def test_local_audio_reaches_caption_view(qapp):
    controller = CaptionController(
        source_factory=lambda _device: FiniteSource(speech_frames()),
        local_factory=FakeLocalEngine,
        voice_detector_factory=lambda: lambda frame: float(np.mean(np.abs(frame))) > 0.01,
    )
    views = []
    controller.view_changed.connect(views.append)
    controller.start(Settings())
    for _ in range(50):
        QTest.qWait(20)
        if views:
            break
    controller.stop()
    assert views and views[0].primary == "Guten Tag"


def test_default_source_waits_for_missing_device_then_reports_listening(qapp):
    class Speaker:
        id = "later"
        name = "Reconnected headset"

    class Recorder:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def record(self, numframes):
            time.sleep(0.01)
            return np.zeros((numframes, 2), dtype=np.float32)

    class Backend:
        ready = False

        def default_speaker(self):
            return Speaker() if self.ready else None

        def all_speakers(self):
            return [Speaker()] if self.ready else []

        def get_microphone(self, **_kwargs):
            return type("Microphone", (), {"recorder": lambda *_a, **_k: Recorder()})()

    backend = Backend()
    controller = CaptionController(
        source_factory=lambda device: LoopbackSource(
            device, backend=backend, retry_forever=True, retry_interval=0.01,
        ),
        local_factory=FakeLocalEngine,
        voice_detector_factory=lambda: lambda _frame: False,
    )
    statuses = []
    levels = []
    controller.status_changed.connect(statuses.append)
    controller.level_changed.connect(levels.append)
    try:
        assert controller.start(Settings())
        for _ in range(50):
            QTest.qWait(20)
            if any("等待系统默认" in message for message in statuses):
                break
        assert any("等待系统默认" in message for message in statuses)
        backend.ready = True
        for _ in range(50):
            QTest.qWait(20)
            if any("正在监听：Reconnected headset" in message for message in statuses):
                break
        assert any("正在监听：Reconnected headset" in message for message in statuses)
        backend.ready = False
        for _ in range(70):
            time.sleep(0.02)
            qapp.processEvents()
            if statuses and "等待系统默认" in statuses[-1]:
                break
        assert "等待系统默认" in statuses[-1]
        assert levels[-1] is None
    finally:
        controller.stop()


def test_audio_level_updates_are_emitted_and_old_sessions_are_ignored(qapp):
    controller = CaptionController(
        source_factory=lambda _device: FiniteSource([
            np.full(1600, 0.2, dtype=np.float32) for _ in range(5)
        ]),
        local_factory=FakeLocalEngine,
        voice_detector_factory=lambda: lambda _frame: False,
    )
    levels = []
    controller.level_changed.connect(levels.append)
    try:
        controller.start(Settings())
        for _ in range(50):
            QTest.qWait(20)
            if any(isinstance(level, float) and level > 0.1 for level in levels):
                break
        assert any(isinstance(level, float) and level > 0.1 for level in levels)
        old_id = controller.session_id
        controller.start(Settings())
        before = len(levels)
        controller._level_from_worker.emit(old_id, 0.9)
        QTest.qWait(20)
        assert len(levels) == before
    finally:
        controller.stop()


def test_audio_level_returns_to_waiting_when_capture_ends(qapp):
    controller = CaptionController(
        source_factory=lambda _device: FiniteSource([
            np.full(1600, 0.2, dtype=np.float32) for _ in range(5)
        ]),
        local_factory=FakeLocalEngine,
        voice_detector_factory=lambda: lambda _frame: False,
    )
    levels = []
    controller.level_changed.connect(levels.append)
    try:
        controller.start(Settings())
        for _ in range(50):
            QTest.qWait(20)
            if (not controller.running and
                    any(isinstance(level, float) and level > 0.1 for level in levels) and
                    levels[-1] is None):
                break
        assert any(isinstance(level, float) and level > 0.1 for level in levels)
        assert levels[-1] is None
    finally:
        controller.stop()


def test_long_local_speech_shows_provisional_then_final_caption(qapp):
    voice = np.full(1600, 0.2, dtype=np.float32)
    silence = np.zeros(1600, dtype=np.float32)
    frames = [voice] * 32 + [silence] * 4

    class LengthAwareEngine:
        warning = None

        def process(self, audio, _rate, session, segment, _lock):
            return CaptionEvent(session, segment, "de", f"{len(audio)} samples", None, True, time.monotonic())

    controller = CaptionController(
        source_factory=lambda _device: FiniteSource(frames),
        local_factory=LengthAwareEngine,
        voice_detector_factory=lambda: lambda frame: bool(np.max(frame) > 0.01),
    )
    views = []
    controller.view_changed.connect(views.append)
    try:
        assert controller.start(Settings())
        for _ in range(150):
            QTest.qWait(20)
            if any(view.final for view in views):
                break
        assert any(not view.final for view in views), views
        assert any(view.final for view in views), views
        assert len({view.segment_id for view in views}) == 1
        assert views[-1].final is True
    finally:
        controller.stop()


def test_listening_status_waits_for_first_capture_frame(qapp):
    entered = threading.Event()
    ready = threading.Event()

    class SlowOpeningSource:
        def frames(self, stop):
            entered.set()
            ready.wait(2)
            if not stop.is_set():
                yield np.zeros(1600, dtype=np.float32)

    controller = CaptionController(source_factory=lambda _device: SlowOpeningSource(), local_factory=FakeLocalEngine)
    statuses = []
    controller.status_changed.connect(statuses.append)
    try:
        controller.start(Settings())
        assert entered.wait(2)
        QTest.qWait(50)
        assert not any("正在监听" in status for status in statuses)
        ready.set()
        for _ in range(50):
            QTest.qWait(20)
            if any("正在监听" in status for status in statuses):
                break
        assert any("正在监听" in status for status in statuses)
    finally:
        ready.set()
        controller.stop()


def test_listening_status_tracks_default_playback_device(qapp):
    class SwitchingSource:
        active_device = None

        def frames(self, _stop):
            for device_id, name in (("one", "Speakers"), ("two", "Headphones")):
                self.active_device = OutputDevice(device_id, name, True)
                yield np.zeros(1600, dtype=np.float32)
                time.sleep(0.03)

    source = SwitchingSource()
    controller = CaptionController(
        source_factory=lambda _device: source,
        local_factory=FakeLocalEngine,
        voice_detector_factory=lambda: lambda _frame: False,
    )
    statuses = []
    controller.status_changed.connect(statuses.append)
    try:
        controller.start(Settings())
        for _ in range(100):
            QTest.qWait(20)
            if any("Headphones" in status for status in statuses):
                break
        assert any("Speakers" in status for status in statuses)
        assert any("Headphones" in status for status in statuses)
    finally:
        controller.stop()


@pytest.mark.parametrize("same_device", [False, True])
def test_local_device_switch_discards_unfinished_previous_speech(qapp, same_device):
    class SwitchingSource:
        active_device = None
        capture_generation = 0

        def frames(self, _stop):
            self.capture_generation = 1
            self.active_device = OutputDevice("one", "Speakers", True)
            for _ in range(2):
                yield np.full(1600, 0.1, dtype=np.float32)
            self.capture_generation = 2
            self.active_device = OutputDevice(
                "one" if same_device else "two", "Speakers" if same_device else "Headphones", True
            )
            for _ in range(3):
                yield np.full(1600, 0.2, dtype=np.float32)
            for _ in range(4):
                yield np.zeros(1600, dtype=np.float32)

    processed = []

    class RecordingEngine:
        warning = None

        def process(self, audio, *_args):
            processed.append(audio.copy())
            return None

    class Detector:
        resets = 0

        def __call__(self, frame):
            return bool(np.max(frame) > 0.01)

        def reset(self):
            self.resets += 1

    detector = Detector()

    controller = CaptionController(
        source_factory=lambda _device: SwitchingSource(),
        local_factory=RecordingEngine,
        voice_detector_factory=lambda: detector,
    )
    try:
        controller.start(Settings())
        for _ in range(100):
            QTest.qWait(20)
            if processed:
                break
        assert processed
        assert detector.resets == 1
        assert all(not np.any(np.isclose(clip, 0.1)) for clip in processed)
        assert any(np.any(np.isclose(clip, 0.2)) for clip in processed)
    finally:
        controller.stop()


def test_online_device_switch_restarts_translation_stream(qapp):
    class SwitchingSource:
        active_device = None
        capture_generation = 0

        def frames(self, _stop):
            for generation in (1, 2):
                self.capture_generation = generation
                self.active_device = OutputDevice("one", "Speakers", True)
                yield np.ones(1600, dtype=np.float32)

    engines = []

    class RecordingEngine:
        def __init__(self):
            self.started = 0
            self.frames = 0
            self.stopped = 0
            self.on_caption = None

        def start(self, _session_id, _language, on_caption, _on_error):
            self.started += 1
            self.on_caption = on_caption

        def push_frame(self, _frame):
            self.frames += 1

        def stop(self):
            self.stopped += 1

    def online_factory(_limit):
        engine = RecordingEngine()
        engines.append(engine)
        return engine

    controller = CaptionController(
        source_factory=lambda _device: SwitchingSource(),
        online_factory=online_factory,
    )
    views = []
    controller.view_changed.connect(views.append)
    try:
        controller.start(replace(Settings(), mode="online"))
        for _ in range(100):
            QTest.qWait(20)
            if len(engines) == 2 and engines[-1].frames and engines[-1].stopped:
                break
        assert len(engines) == 2
        assert [engine.frames for engine in engines] == [1, 1]
        assert all(engine.started == 1 for engine in engines)
        assert all(engine.stopped == 1 for engine in engines)
        engines[0].on_caption(CaptionEvent(controller.session_id, "old", "de", "old", None, True, time.monotonic()))
        engines[1].on_caption(CaptionEvent(controller.session_id, "new", "de", "new", None, True, time.monotonic()))
        QTest.qWait(30)
        assert [view.primary for view in views] == ["new"]
    finally:
        controller.stop()


def test_old_inference_result_is_not_shown_after_device_switch(qapp):
    old_started = threading.Event()
    release_old = threading.Event()

    class SwitchingSource:
        active_device = None

        def frames(self, _stop):
            self.active_device = OutputDevice("one", "Speakers", True)
            for frame in speech_frames():
                yield frame * 0.5
            assert old_started.wait(2)
            self.active_device = OutputDevice("two", "Headphones", True)
            for frame in speech_frames():
                yield frame
            release_old.set()

    class SlowEngine:
        warning = None

        def process(self, audio, _rate, session, segment, _lock):
            text = "old" if np.max(audio) < 0.15 else "new"
            if text == "old":
                old_started.set()
                assert release_old.wait(2)
            return CaptionEvent(session, segment, "de", text, None, True, time.monotonic())

    controller = CaptionController(
        source_factory=lambda _device: SwitchingSource(),
        local_factory=SlowEngine,
        voice_detector_factory=lambda: lambda frame: bool(np.max(frame) > 0.01),
    )
    views = []
    controller.view_changed.connect(views.append)
    try:
        controller.start(Settings())
        for _ in range(100):
            QTest.qWait(20)
            if any(view.primary == "new" for view in views):
                break
        assert [view.primary for view in views] == ["new"]
    finally:
        release_old.set()
        controller.stop()


def test_old_session_event_is_ignored_after_restart(qapp):
    controller = CaptionController(
        source_factory=lambda _device: FiniteSource([]),
        local_factory=FakeLocalEngine,
    )
    views = []
    controller.view_changed.connect(views.append)
    controller.start(Settings())
    old_id = controller.session_id
    controller.start(Settings())
    controller.accept_caption(CaptionEvent(old_id, "old", "de", "Alt", None, True, time.monotonic()))
    assert views == []
    controller.stop()


def test_comparison_toggle_uses_current_caption_without_new_recognition(qapp):
    controller = CaptionController(source_factory=lambda _device: FiniteSource([]))
    views = []
    controller.view_changed.connect(views.append)
    controller.start(Settings())
    controller.accept_caption(CaptionEvent(controller.session_id, "en", "en", "Hello", "Hallo", True, time.monotonic()))
    controller.set_compare_original(True)
    assert views[-1].secondary == "Hello"
    controller.stop()


def test_online_setup_failure_is_visible_and_capture_never_starts(qapp):
    calls = []

    class BrokenOnline:
        def start(self, *_args):
            raise OnlineUnavailable("Azure credentials are not configured")

        def stop(self):
            pass

    controller = CaptionController(
        source_factory=lambda _device: calls.append("capture") or FiniteSource([]),
        online_factory=lambda _limit: BrokenOnline(),
    )
    statuses = []
    controller.status_changed.connect(statuses.append)
    controller.start(replace(Settings(), mode="online"))
    for _ in range(50):
        QTest.qWait(20)
        if any("credentials" in status for status in statuses):
            break
    controller.stop()
    assert calls == []
    assert any("credentials" in status for status in statuses)


def test_stop_sets_no_active_worker(qapp):
    controller = CaptionController(source_factory=lambda _device: FiniteSource([]), local_factory=FakeLocalEngine)
    controller.start(Settings())
    controller.stop()
    assert not controller.running


def test_queued_caption_is_ignored_after_pause(qapp):
    controller = CaptionController(source_factory=lambda _device: FiniteSource([]))
    views = []
    controller.view_changed.connect(views.append)
    controller.start(Settings())
    event = CaptionEvent(controller.session_id, "old", "de", "Alt", None, True, time.monotonic())
    worker = threading.Thread(target=lambda: controller._caption_from_worker.emit(event))
    worker.start()
    worker.join()
    controller.pause(True)
    QApplication.processEvents()
    assert views == []


def test_stuck_worker_blocks_new_session(qapp, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    class BlockingSource:
        def frames(self, _stop):
            entered.set()
            release.wait(2)
            yield np.zeros(1600, dtype=np.float32)

    controller = CaptionController(source_factory=lambda _device: BlockingSource(), local_factory=FakeLocalEngine)
    monkeypatch.setattr(controller, "STOP_JOIN_SECONDS", 0.01, raising=False)
    try:
        assert controller.start(Settings()) is True
        assert entered.wait(1)
        old_id = controller.session_id
        assert controller.start(Settings()) is False
        assert controller.session_id != old_id
        assert controller.running
    finally:
        release.set()
        controller.stop()


def test_local_model_failure_is_reported_before_audio_capture(qapp):
    calls = []

    class BrokenEngine:
        def prepare(self):
            raise RuntimeError("model unavailable")

    controller = CaptionController(
        source_factory=lambda _device: calls.append("capture") or FiniteSource([]),
        local_factory=BrokenEngine,
    )
    statuses = []
    controller.status_changed.connect(statuses.append)
    controller.start(Settings())
    for _ in range(50):
        QTest.qWait(20)
        if any("model unavailable" in status for status in statuses):
            break
    controller.stop()
    assert calls == []
    assert any("model unavailable" in status for status in statuses)
