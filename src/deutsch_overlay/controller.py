"""Session lifecycle between loopback capture, caption engines, and Qt UI."""

from __future__ import annotations

import os
import queue
import threading
from dataclasses import replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from deutsch_overlay.audio import AudioLevelMeter, LoopbackSource, SileroSpeechDetector, SpeechSegmenter
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
    STOP_JOIN_SECONDS = 1.0
    CAPTURE_JOIN_SECONDS = 0.25
    LOCAL_PREVIEW_FRAMES = 20
    ONLINE_ACTIVITY_RMS = 0.0001
    ONLINE_IDLE_FRAMES = 50  # Five seconds of 100 ms frames.
    view_changed = Signal(object)
    status_changed = Signal(str)
    level_changed = Signal(object)
    _caption_from_worker = Signal(object)
    _status_from_worker = Signal(int, str)
    _level_from_worker = Signal(int, object)

    def __init__(self, *, source_factory=None, local_factory=None, online_factory=None, voice_detector_factory=None) -> None:
        super().__init__()
        self.source_factory = source_factory or (lambda device: LoopbackSource(device, retry_forever=True))
        self.local_factory = local_factory or LocalEngine
        self.online_factory = online_factory or _online_engine
        self.voice_detector_factory = voice_detector_factory or SileroSpeechDetector
        self._local_engine = None
        self._generation = 0
        self._device_epoch = 0
        self._reducer = CaptionReducer(0)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._settings = Settings()
        self._caption_from_worker.connect(self.accept_caption)
        self._status_from_worker.connect(self._accept_status)
        self._level_from_worker.connect(self._accept_audio_level)

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
        self._device_epoch = 0
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
        self.level_changed.emit(None)
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
        if event.device_epoch != self._device_epoch:
            return
        view = self._reducer.apply(event)
        if view is not None:
            self.view_changed.emit(view)

    @Slot(int, str)
    def _accept_status(self, session_id: int, message: str) -> None:
        if session_id == self._generation:
            self.status_changed.emit(message)

    @Slot(int, object)
    def _accept_audio_level(self, session_id: int, level: float | None) -> None:
        if session_id == self._generation:
            self.level_changed.emit(level)

    def _source_for_session(self, device_id: str | None, session_id: int):
        source = self.source_factory(device_id)
        if isinstance(source, LoopbackSource):
            def report_wait(message: str) -> None:
                self._level_from_worker.emit(session_id, None)
                self._status_from_worker.emit(session_id, message)

            source.on_status = report_wait
        return source

    def _run_local(self, session_id: int, settings: Settings, stop: threading.Event) -> None:
        clips: queue.Queue[tuple[str, object, bool, int]] = queue.Queue(maxsize=4)
        capture_done = threading.Event()
        device_epoch = 0
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
                    segment_id, clip, final, epoch = clips.get(timeout=0.1)
                except queue.Empty:
                    continue
                if epoch != device_epoch:
                    continue
                try:
                    language = settings.language_lock if settings.language_lock != "auto" else None
                    event = engine.process(clip, 16000, session_id, segment_id, language)
                    if event is not None and epoch == device_epoch:
                        self._caption_from_worker.emit(replace(event, final=final, device_epoch=epoch))
                    if getattr(engine, "warning", None):
                        self._status_from_worker.emit(session_id, engine.warning)
                        engine.warning = None
                except Exception as exc:
                    self._status_from_worker.emit(session_id, f"本地识别失败：{exc}")
                    stop.set()
                    break

        inference = threading.Thread(target=infer, name=f"inference-{session_id}", daemon=True)
        inference.start()
        def fresh_segmenter() -> SpeechSegmenter:
            return SpeechSegmenter(
                sample_rate=16000, frame_samples=1600, silence_seconds=0.3,
                max_seconds=7.0, voice_detector=voice_detector,
            )

        segmenter = fresh_segmenter()
        level_meter = AudioLevelMeter()
        counter = 0
        last_preview_frame = 0

        def queue_clip(clip, *, final: bool = True) -> None:
            nonlocal counter
            if not final and not clips.empty():
                return
            segment_id = f"{session_id}-{counter + 1}"
            if final:
                counter += 1
            if clips.full():
                if not final:
                    return
                try:
                    clips.get_nowait()
                except queue.Empty:
                    pass
                self._status_from_worker.emit(session_id, "识别速度落后，已跳过一段旧语音")
            clips.put_nowait((segment_id, clip, final, device_epoch))

        try:
            self._status_from_worker.emit(session_id, "正在连接电脑播放设备（本地模式）")
            listening = False
            active_id = None
            active_generation = None
            source = self._source_for_session(settings.output_device_id, session_id)
            for frame in source.frames(stop):
                if stop.is_set():
                    break
                active = getattr(source, "active_device", None)
                generation = getattr(source, "capture_generation", None)
                changed = listening and (
                    (active is not None and active.id != active_id)
                    or (generation is not None and generation != active_generation)
                )
                if changed:
                    device_epoch += 1
                    self._device_epoch = device_epoch
                    reset_detector = getattr(voice_detector, "reset", None)
                    if reset_detector is not None:
                        reset_detector()
                    segmenter = fresh_segmenter()
                    level_meter = AudioLevelMeter()
                    while not clips.empty():
                        try:
                            clips.get_nowait()
                        except queue.Empty:
                            break
                    last_preview_frame = 0
                if not listening or changed:
                    name = f"：{active.name}" if active is not None else "电脑播放声"
                    self._status_from_worker.emit(session_id, f"正在监听{name}（本地模式）")
                    active_id = active.id if active is not None else None
                    active_generation = generation
                    listening = True
                level = level_meter.push(frame)
                if level is not None:
                    self._level_from_worker.emit(session_id, level)
                completed = segmenter.push(frame)
                for clip in completed:
                    queue_clip(clip)
                if completed:
                    last_preview_frame = 0
                elif segmenter.active_frames >= last_preview_frame + self.LOCAL_PREVIEW_FRAMES:
                    preview = segmenter.snapshot()
                    if preview is not None:
                        queue_clip(preview, final=False)
                        last_preview_frame = segmenter.active_frames
            if not stop.is_set():
                clip = segmenter.flush()
                if clip is not None:
                    queue_clip(clip)
        except Exception as exc:
            self._status_from_worker.emit(session_id, f"电脑声音采集失败：{exc}")
        finally:
            capture_done.set()
            self._level_from_worker.emit(session_id, None)
            inference.join()

    def _run_online(self, session_id: int, settings: Settings, stop: threading.Event) -> None:
        engine = None
        level_meter = AudioLevelMeter()
        device_epoch = 0
        stream_number = 0
        streaming = False
        quiet_frames = 0
        capture_thread = None

        def on_caption_for(epoch: int, stream: int):
            return lambda event: self._caption_from_worker.emit(replace(
                event, segment_id=f"{stream}:{event.segment_id}", device_epoch=epoch,
            ))

        def new_engine():
            candidate = self.online_factory(settings.online_minutes_limit)
            preflight = getattr(candidate, "preflight", None)
            if preflight is not None:
                preflight()
            return candidate

        try:
            engine = new_engine()
            language = settings.language_lock if settings.language_lock != "auto" else None
            self._status_from_worker.emit(session_id, "正在等待电脑播放设备（在线模式，尚未发送音频）")
            listening = False
            active_id = None
            active_generation = None
            source = self._source_for_session(settings.output_device_id, session_id)
            packets: queue.Queue[tuple[object, object, object]] = queue.Queue(maxsize=120)
            capture_done = threading.Event()
            capture_errors: list[Exception] = []

            def capture() -> None:
                reported_overflow = False
                try:
                    for captured in source.frames(stop):
                        if stop.is_set():
                            break
                        packet = (
                            captured.copy(),
                            getattr(source, "active_device", None),
                            getattr(source, "capture_generation", None),
                        )
                        try:
                            packets.put_nowait(packet)
                        except queue.Full:
                            try:
                                packets.get_nowait()
                            except queue.Empty:
                                pass
                            packets.put_nowait(packet)
                            if not reported_overflow:
                                self._status_from_worker.emit(session_id, "在线连接较慢，已跳过最早的一段声音")
                                reported_overflow = True
                except Exception as exc:
                    capture_errors.append(exc)
                finally:
                    capture_done.set()

            capture_thread = threading.Thread(target=capture, name=f"online-capture-{session_id}", daemon=True)
            capture_thread.start()
            while not stop.is_set():
                try:
                    frame, active, generation = packets.get(timeout=0.1)
                except queue.Empty:
                    if capture_done.is_set():
                        break
                    continue
                if stop.is_set():
                    break
                changed = listening and (
                    (active is not None and active.id != active_id)
                    or (generation is not None and generation != active_generation)
                )
                if changed:
                    device_epoch += 1
                    self._device_epoch = device_epoch
                    if engine is not None:
                        engine.stop()
                    engine = None
                    streaming = False
                    quiet_frames = 0
                    level_meter = AudioLevelMeter()
                if not listening or changed:
                    name = f"：{active.name}" if active is not None else "电脑播放声"
                    self._status_from_worker.emit(session_id, f"正在监听{name}（在线待机，等待播放声音）")
                    active_id = active.id if active is not None else None
                    active_generation = generation
                    listening = True
                level = level_meter.push(frame)
                if level is not None:
                    self._level_from_worker.emit(session_id, level)
                audible = float(np.sqrt(np.mean(np.square(frame, dtype=np.float32)))) >= self.ONLINE_ACTIVITY_RMS
                if audible and not streaming:
                    if engine is None:
                        engine = new_engine()
                    stream_number += 1
                    engine.start(
                        session_id,
                        language,
                        on_caption_for(device_epoch, stream_number),
                        lambda message: self._status_from_worker.emit(session_id, message),
                    )
                    if stop.is_set():
                        break
                    streaming = True
                    self._status_from_worker.emit(session_id, "正在在线识别电脑播放声（可能产生费用）")
                if streaming:
                    engine.push_frame(frame)
                    quiet_frames = 0 if audible else quiet_frames + 1
                    if quiet_frames >= self.ONLINE_IDLE_FRAMES:
                        finish = getattr(engine, "finish", None)
                        if finish is not None:
                            finish()
                        else:
                            engine.stop()
                        engine = None
                        streaming = False
                        quiet_frames = 0
                        self._status_from_worker.emit(session_id, "在线识别待机：播放声已停止，暂停发送音频")
            if capture_errors and not stop.is_set():
                self._status_from_worker.emit(session_id, f"电脑声音采集失败：{capture_errors[0]}")
        except (OnlineUnavailable, OnlineLimitReached) as exc:
            self._status_from_worker.emit(session_id, f"在线识别停止：{exc}")
        except Exception:
            self._status_from_worker.emit(session_id, "在线识别停止：请检查网络或服务状态")
        finally:
            stop.set()
            self._level_from_worker.emit(session_id, None)
            try:
                if engine is not None:
                    engine.stop()
            finally:
                if capture_thread is not None:
                    capture_thread.join(timeout=self.CAPTURE_JOIN_SECONDS)
