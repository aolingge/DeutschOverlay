"""Session lifecycle between loopback capture, caption engines, and Qt UI."""

from __future__ import annotations

import os
import queue
import threading
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from deutsch_overlay.audio import LoopbackSource, SileroSpeechDetector, SpeechSegmenter
from deutsch_overlay.captions import CaptionEvent, CaptionReducer
from deutsch_overlay.config import Settings
from deutsch_overlay.credentials import AzureCredentialStore
from deutsch_overlay.engines.azure import (
    AzureEngine,
    OnlineBudget,
    OnlineLimitReached,
    OnlineUnavailable,
)
from deutsch_overlay.engines.local import LocalEngine


def _online_engine(limit_minutes: int) -> AzureEngine:
    local_data = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DeutschOverlay"
    return AzureEngine(
        AzureCredentialStore(),
        OnlineBudget(local_data / "online-usage.json", limit_minutes),
    )


class CaptionController(QObject):
    STOP_JOIN_SECONDS = 3.0
    view_changed = Signal(object)
    status_changed = Signal(str)
    _caption_from_worker = Signal(object)
    _status_from_worker = Signal(int, str)

    def __init__(self, *, source_factory=None, local_factory=None, online_factory=None, voice_detector_factory=None) -> None:
        super().__init__()
        self.source_factory = source_factory or (lambda device: LoopbackSource(device))
        self.local_factory = local_factory or LocalEngine
        self.online_factory = online_factory or _online_engine
        self.voice_detector_factory = voice_detector_factory or SileroSpeechDetector
        self._local_engine = None
        self._generation = 0
        self._reducer = CaptionReducer(0)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._settings = Settings()
        self._caption_from_worker.connect(self.accept_caption)
        self._status_from_worker.connect(self._accept_status)

    @property
    def session_id(self) -> int:
        return self._generation

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, settings: Settings) -> bool:
        self.stop()
        if self.running:
            self.status_changed.emit("旧识别会话仍在退出，请稍后再次应用设置")
            return False
        self._generation += 1
        self._settings = settings
        self._reducer = CaptionReducer(self._generation, settings.compare_original)
        self._stop = threading.Event()
        target = self._run_online if settings.mode == "online" else self._run_local
        self._thread = threading.Thread(
            target=target,
            args=(self._generation, settings, self._stop),
            name=f"caption-session-{self._generation}",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self._generation += 1
        self._reducer = CaptionReducer(self._generation, self._settings.compare_original)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=self.STOP_JOIN_SECONDS)
        if not self.running:
            self._thread = None

    def pause(self, paused: bool) -> None:
        if paused:
            self.stop()
            self.status_changed.emit("字幕识别已暂停")
        else:
            self.start(self._settings)

    def set_compare_original(self, enabled: bool) -> None:
        self._settings = replace(self._settings, compare_original=enabled)
        view = self._reducer.set_compare_original(enabled)
        if view is not None:
            self.view_changed.emit(view)

    @Slot(object)
    def accept_caption(self, event: CaptionEvent) -> None:
        view = self._reducer.apply(event)
        if view is not None:
            self.view_changed.emit(view)

    @Slot(int, str)
    def _accept_status(self, session_id: int, message: str) -> None:
        if session_id == self._generation:
            self.status_changed.emit(message)

    def _run_local(self, session_id: int, settings: Settings, stop: threading.Event) -> None:
        clips: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=4)
        capture_done = threading.Event()
        engine = self._local_engine or self.local_factory()
        self._local_engine = engine
        try:
            self._status_from_worker.emit(session_id, "正在加载本地识别与翻译模型，请稍候")
            prepare = getattr(engine, "prepare", None)
            if prepare is not None:
                prepare()
            if getattr(engine, "warning", None):
                self._status_from_worker.emit(session_id, engine.warning)
                engine.warning = None
        except Exception as exc:
            self._status_from_worker.emit(session_id, f"本地模型加载失败：{exc}")
            return
        if stop.is_set():
            return
        try:
            voice_detector = self.voice_detector_factory()
        except (ImportError, OSError, RuntimeError) as exc:
            voice_detector = None
            self._status_from_worker.emit(session_id, f"语音检测不可用，已使用音量门限（{type(exc).__name__}）")

        def infer() -> None:
            while not capture_done.is_set() or not clips.empty():
                try:
                    segment_id, clip = clips.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    language = settings.language_lock if settings.language_lock != "auto" else None
                    event = engine.process(clip, 16000, session_id, segment_id, language)
                    if event is not None:
                        self._caption_from_worker.emit(event)
                    if getattr(engine, "warning", None):
                        self._status_from_worker.emit(session_id, engine.warning)
                        engine.warning = None
                except Exception as exc:
                    self._status_from_worker.emit(session_id, f"本地识别失败：{exc}")
                    stop.set()
                    break

        inference = threading.Thread(target=infer, name=f"inference-{session_id}", daemon=True)
        inference.start()
        segmenter = SpeechSegmenter(
            sample_rate=16000, frame_samples=1600, silence_seconds=0.3,
            max_seconds=5.0, voice_detector=voice_detector,
        )
        counter = 0

        def queue_clip(clip) -> None:
            nonlocal counter
            counter += 1
            if clips.full():
                try:
                    clips.get_nowait()
                except queue.Empty:
                    pass
                self._status_from_worker.emit(session_id, "识别速度落后，已跳过一段旧语音")
            clips.put_nowait((f"{session_id}-{counter}", clip))

        try:
            self._status_from_worker.emit(session_id, "正在监听电脑播放声（本地模式）")
            for frame in self.source_factory(settings.output_device_id).frames(stop):
                if stop.is_set():
                    break
                for clip in segmenter.push(frame):
                    queue_clip(clip)
            if not stop.is_set():
                clip = segmenter.flush()
                if clip is not None:
                    queue_clip(clip)
        except Exception as exc:
            self._status_from_worker.emit(session_id, f"电脑声音采集失败：{exc}")
        finally:
            capture_done.set()
            inference.join()

    def _run_online(self, session_id: int, settings: Settings, stop: threading.Event) -> None:
        engine = None
        try:
            engine = self.online_factory(settings.online_minutes_limit)
            language = settings.language_lock if settings.language_lock != "auto" else None
            engine.start(
                session_id,
                language,
                self._caption_from_worker.emit,
                lambda message: self._status_from_worker.emit(session_id, message),
            )
            self._status_from_worker.emit(session_id, "正在监听电脑播放声（在线模式，可能产生费用）")
            for frame in self.source_factory(settings.output_device_id).frames(stop):
                if stop.is_set():
                    break
                engine.push_frame(frame)
        except (OnlineUnavailable, OnlineLimitReached) as exc:
            self._status_from_worker.emit(session_id, f"在线识别停止：{exc}")
        except Exception:
            self._status_from_worker.emit(session_id, "在线识别停止：请检查网络或服务状态")
        finally:
            if engine is not None:
                engine.stop()
