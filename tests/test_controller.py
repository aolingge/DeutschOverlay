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


def test_online_setup_failure_is_visible_before_capture_opens(qapp):
    calls = []

    class BrokenOnline:
        def preflight(self):
            raise OnlineUnavailable("Azure credentials are not configured")

        def start(self, *_args):
            raise AssertionError("cloud connection should not start")

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


def test_online_connection_waits_for_playback_device_first_frame(qapp):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    class DelayedSource:
        active_device = OutputDevice("one", "Speakers", True)
        capture_generation = 1

        def frames(self, stop):
            entered.set()
            release.wait(2)
            if not stop.is_set():
                yield np.full(1600, 0.1, dtype=np.float32)

    class RecordingOnline:
        def start(self, *_args):
            calls.append("start")

        def push_frame(self, _frame):
            calls.append("frame")

        def stop(self):
            calls.append("stop")

    controller = CaptionController(
        source_factory=lambda _device: DelayedSource(),
        online_factory=lambda _limit: RecordingOnline(),
    )
    try:
        assert controller.start(replace(Settings(), mode="online"))
        assert entered.wait(1)
        QTest.qWait(50)
        assert calls == []
        release.set()
        for _ in range(50):
            QTest.qWait(20)
            if "frame" in calls:
                break
        assert calls[:2] == ["start", "frame"]
    finally:
        release.set()
        controller.stop()


def test_online_silent_playback_does_not_open_or_bill_cloud_session(qapp):
    starts = []

    class RecordingOnline:
        def start(self, *_args):
            starts.append("start")

        def push_frame(self, _frame):
            raise AssertionError("silence must not be sent online")

        def stop(self):
            pass

    controller = CaptionController(
        source_factory=lambda _device: FiniteSource([np.zeros(1600, dtype=np.float32)] * 10),
        online_factory=lambda _limit: RecordingOnline(),
    )
    try:
        assert controller.start(replace(Settings(), mode="online"))
        for _ in range(50):
            QTest.qWait(20)
            if not controller.running:
                break
        assert starts == []
    finally:
        controller.stop()


def test_online_idle_disconnects_then_new_sound_starts_fresh_session(qapp):
    engines = []

    class RecordingOnline:
        def __init__(self):
            self.frames = 0
            self.starts = 0
            self.stops = 0

        def start(self, *_args):
            self.starts += 1

        def push_frame(self, _frame):
            self.frames += 1

        def stop(self):
            self.stops += 1

    def make_engine(_limit):
        engine = RecordingOnline()
        engines.append(engine)
        return engine

    loud = np.full(1600, 0.1, dtype=np.float32)
    silence = np.zeros(1600, dtype=np.float32)
    source = FiniteSource([loud, *([silence] * CaptionController.ONLINE_IDLE_FRAMES), loud])
    controller = CaptionController(source_factory=lambda _device: source, online_factory=make_engine)
    try:
        assert controller.start(replace(Settings(), mode="online"))
        for _ in range(50):
            QTest.qWait(20)
            if not controller.running:
                break
        assert len(engines) == 2
        assert engines[0].starts == engines[1].starts == 1
        assert engines[0].frames == 1 + CaptionController.ONLINE_IDLE_FRAMES
        assert engines[1].frames == 1
    finally:
        controller.stop()


def test_online_idle_delivers_final_caption_from_graceful_finish(qapp):
    class FinalOnFinish:
        def start(self, session, _language, on_caption, _on_error):
            self.session = session
            self.on_caption = on_caption

        def push_frame(self, _frame):
            pass

        def finish(self):
            self.on_caption(CaptionEvent(
                self.session, "last", "de", "Guten Tag", None, True, time.monotonic(),
            ))

        def stop(self):
            pass

    loud = np.full(1600, 0.1, dtype=np.float32)
    silence = np.zeros(1600, dtype=np.float32)
    controller = CaptionController(
        source_factory=lambda _device: FiniteSource(
            [loud, *([silence] * CaptionController.ONLINE_IDLE_FRAMES)]
        ),
        online_factory=lambda _limit: FinalOnFinish(),
    )
    views = []
    controller.view_changed.connect(views.append)
    try:
        assert controller.start(replace(Settings(), mode="online"))
        for _ in range(50):
            QTest.qWait(20)
            if not controller.running and views:
                break
        assert [view.primary for view in views] == ["Guten Tag"]
    finally:
        controller.stop()


def test_online_reconnect_can_reuse_engine_segment_id(qapp):
    class ReusedSegmentOnline:
        def start(self, session, _language, on_caption, _on_error):
            self.session = session
            self.on_caption = on_caption
            self.sent_partial = False

        def push_frame(self, frame):
            if not self.sent_partial and np.max(frame) > 0:
                self.sent_partial = True
                self.on_caption(CaptionEvent(
                    self.session, "shared", "de", "Guten", None, False, time.monotonic(),
                ))

        def finish(self):
            self.on_caption(CaptionEvent(
                self.session, "shared", "de", "Guten Tag", None, True, time.monotonic(),
            ))

        def stop(self):
            pass

    loud = np.full(1600, 0.1, dtype=np.float32)
    silence = np.zeros(1600, dtype=np.float32)
    frames = [loud, *([silence] * CaptionController.ONLINE_IDLE_FRAMES)] * 2
    controller = CaptionController(
        source_factory=lambda _device: FiniteSource(frames),
        online_factory=lambda _limit: ReusedSegmentOnline(),
    )
    views = []
    controller.view_changed.connect(views.append)
    try:
        assert controller.start(replace(Settings(), mode="online"))
        for _ in range(150):
            QTest.qWait(20)
            if not controller.running and len(views) >= 4:
                break
        assert [view.final for view in views] == [False, True, False, True]
        assert views[0].segment_id != views[2].segment_id
    finally:
        controller.stop()


def test_online_stop_during_cloud_connection_never_uploads_audio(qapp, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    frames = []

    class BlockingOnline:
        def start(self, *_args):
            entered.set()
            release.wait(2)

        def push_frame(self, _frame):
            frames.append("uploaded")

        def stop(self):
            pass

    controller = CaptionController(
        source_factory=lambda _device: FiniteSource([np.full(1600, 0.1, dtype=np.float32)]),
        online_factory=lambda _limit: BlockingOnline(),
    )
    monkeypatch.setattr(controller, "STOP_JOIN_SECONDS", 0.01)
    try:
        assert controller.start(replace(Settings(), mode="online"))
        assert entered.wait(1)
        controller.stop()
        release.set()
        for _ in range(50):
            QTest.qWait(20)
            if not controller.running:
                break
        assert not controller.running
        assert frames == []
    finally:
        release.set()
        controller.stop()


def test_online_capture_continues_while_cloud_connection_opens(qapp):
    entered = threading.Event()
    release = threading.Event()
    captured = []
    uploaded = []

    class BurstSource:
        active_device = OutputDevice("one", "Speakers", True)
        capture_generation = 1

        def frames(self, stop):
            for index in range(30):
                if stop.is_set():
                    return
                captured.append(index)
                yield np.full(1600, 0.1, dtype=np.float32)
                time.sleep(0.005)

    class SlowOnline:
        def start(self, *_args):
            entered.set()
            release.wait(2)

        def push_frame(self, frame):
            uploaded.append(float(frame[0]))

        def stop(self):
            pass

    controller = CaptionController(
        source_factory=lambda _device: BurstSource(),
        online_factory=lambda _limit: SlowOnline(),
    )
    try:
        assert controller.start(replace(Settings(), mode="online"))
        assert entered.wait(1)
        deadline = time.monotonic() + 1
        while len(captured) < 30 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(captured) == 30
        release.set()
        for _ in range(100):
            QTest.qWait(20)
            if len(uploaded) == 30:
                break
        assert len(uploaded) == 30
    finally:
        release.set()
        controller.stop()


def test_online_stop_closes_cloud_when_capture_driver_blocks(qapp, monkeypatch):
    capture_blocked = threading.Event()
    release = threading.Event()
    uploaded = threading.Event()
    cloud_stopped = threading.Event()

    class BlockingSource:
        def frames(self, _stop):
            yield np.full(1600, 0.1, dtype=np.float32)
            capture_blocked.set()
            release.wait(3)

    class RecordingOnline:
        def start(self, *_args):
            pass

        def push_frame(self, _frame):
            uploaded.set()

        def stop(self):
            cloud_stopped.set()

    controller = CaptionController(
        source_factory=lambda _device: BlockingSource(),
        online_factory=lambda _limit: RecordingOnline(),
    )
    monkeypatch.setattr(controller, "STOP_JOIN_SECONDS", 0.05)
    try:
        assert controller.start(replace(Settings(), mode="online"))
        assert capture_blocked.wait(1)
        assert uploaded.wait(1)
        controller.stop()
        assert cloud_stopped.wait(0.3)
        for _ in range(25):
            if not controller.running:
                break
            QTest.qWait(20)
        assert not controller.running
    finally:
        release.set()
        controller.stop()


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
