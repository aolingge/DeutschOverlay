import os
import time
import threading
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

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
