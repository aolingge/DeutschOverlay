"""Windows output loopback capture and bounded audio segmentation."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
from math import ceil
from threading import Event
from time import monotonic
from typing import Callable, Iterator
import sys

import numpy as np


class AudioDeviceError(RuntimeError):
    """The selected playback device cannot currently be captured."""


class AudioFormatError(AudioDeviceError):
    """The capture backend returned audio that cannot be interpreted."""


@dataclass(frozen=True, slots=True)
class OutputDevice:
    id: str
    name: str
    is_default: bool


class AudioLevelMeter:
    """Summarize short capture windows for the user-facing input indicator."""

    def __init__(self, *, frames_per_update: int = 5) -> None:
        if frames_per_update <= 0:
            raise ValueError("frames_per_update must be positive")
        self.frames_per_update = frames_per_update
        self._frames = 0
        self._peak_rms = 0.0

    def push(self, frame: np.ndarray) -> float | None:
        if frame.size == 0:
            raise ValueError("audio frame must not be empty")
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float32))))
        self._peak_rms = max(self._peak_rms, rms)
        self._frames += 1
        if self._frames < self.frames_per_update:
            return None
        level = self._peak_rms
        self._frames = 0
        self._peak_rms = 0.0
        return level


def _soundcard():
    import soundcard

    return soundcard


@contextmanager
def _windows_com_apartment():
    """SoundCard initializes COM at import, but capture uses another thread."""
    if sys.platform != "win32":
        yield
        return
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = ()
    result = ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    if result not in (0, 1, -2147417850):  # S_OK, S_FALSE, RPC_E_CHANGED_MODE
        raise AudioDeviceError(f"Windows audio initialization failed: {result & 0xffffffff:#x}")
    try:
        yield
    finally:
        if result in (0, 1):
            ole32.CoUninitialize()


def list_output_devices(backend=None) -> list[OutputDevice]:
    backend = backend or _soundcard()
    with _windows_com_apartment():
        try:
            default = backend.default_speaker()
            default_id = default.id if default is not None else None
            return [
                OutputDevice(str(speaker.id), speaker.name, speaker.id == default_id)
                for speaker in backend.all_speakers()
            ]
        except (OSError, RuntimeError) as exc:
            raise AudioDeviceError("output devices could not be listed") from exc


def _downmix(raw: np.ndarray) -> np.ndarray:
    if raw.ndim == 1:
        return raw
    if raw.ndim != 2 or raw.shape[1] == 0:
        raise AudioFormatError("capture returned an unsupported audio format")
    if not len(raw):
        return np.empty(0, dtype=np.float32)
    channel_rms = np.sqrt(np.mean(np.square(raw, dtype=np.float32), axis=0))
    loudest = int(np.argmax(channel_rms))
    active = channel_rms >= max(1e-7, float(channel_rms[loudest]) * 0.001)
    if not np.any(active):
        return np.zeros(len(raw), dtype=np.float32)
    channels = raw[:, active]
    reference = raw[:, loudest]
    denominator = channel_rms[active] * channel_rms[loudest] + 1e-12
    correlation = np.mean(channels * reference[:, None], axis=0) / denominator
    signs = np.where(correlation < -0.9, -1.0, 1.0).astype(np.float32)
    return np.mean(channels * signs, axis=1, dtype=np.float32)


class LoopbackSource:
    """Yield fixed-size mono float32 frames from one playback endpoint."""

    RECONNECT_SECONDS = 5.0

    def __init__(
        self,
        device_id: str | None = None,
        *,
        backend=None,
        sample_rate: int = 16000,
        frame_samples: int = 1600,
        device_check_frames: int = 20,
        retry_forever: bool = False,
        retry_interval: float = 0.5,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        if sample_rate <= 0 or frame_samples <= 0 or device_check_frames <= 0 or retry_interval <= 0:
            raise ValueError("audio frame and device check intervals must be positive")
        self.device_id = device_id
        self.backend = backend
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.device_check_frames = device_check_frames
        self.retry_forever = retry_forever
        self.retry_interval = retry_interval
        self.on_status = on_status
        self._last_notice: str | None = None
        self.active_device: OutputDevice | None = None
        self.capture_generation = 0

    def _wait_for_device(self, stop_event: Event, message: str) -> None:
        if message != self._last_notice:
            self._last_notice = message
            if self.on_status is not None:
                self.on_status(message)
        stop_event.wait(self.retry_interval)

    def frames(self, stop_event: Event) -> Iterator[np.ndarray]:
        backend = self.backend or _soundcard()
        with _windows_com_apartment():
            yield from self._frames_with_com(stop_event, backend)

    def _frames_with_com(self, stop_event: Event, backend) -> Iterator[np.ndarray]:
        missing_since: float | None = None
        capture_error_since: float | None = None
        while not stop_event.is_set():
            try:
                devices = list_output_devices(backend)
            except AudioDeviceError:
                if self.retry_forever:
                    self._wait_for_device(stop_event, "暂时无法读取播放设备，正在重试")
                    continue
                if self.device_id is not None or self.active_device is None:
                    raise
                devices = []
            selected = next(
                (device for device in devices if device.id == self.device_id), None
            ) if self.device_id else next(
                (device for device in devices if device.is_default), None
            )
            if selected is None:
                if self.retry_forever:
                    message = (
                        "所选播放设备暂时不可用，正在等待重新连接"
                        if self.device_id is not None else "等待系统默认播放设备连接"
                    )
                    self._wait_for_device(stop_event, message)
                    continue
                if self.device_id is None and self.active_device is not None:
                    missing_since = missing_since or monotonic()
                    if monotonic() - missing_since < self.RECONNECT_SECONDS:
                        stop_event.wait(0.1)
                        continue
                raise AudioDeviceError("selected output device is unavailable")
            missing_since = None
            try:
                for frame in self._capture_selected(backend, selected, stop_event):
                    capture_error_since = None
                    self._last_notice = None
                    yield frame
            except (AudioDeviceError, OSError, RuntimeError, ValueError, TypeError) as exc:
                if stop_event.is_set():
                    return
                if isinstance(exc, AudioFormatError):
                    raise
                if isinstance(exc, RuntimeError) and "unsupported format" in str(exc).lower():
                    raise AudioFormatError("选定播放设备的音频格式不受支持 (unsupported format)") from exc
                if isinstance(exc, (ValueError, TypeError)):
                    raise AudioDeviceError("output audio capture failed") from exc
                if self.retry_forever:
                    self._wait_for_device(stop_event, "电脑声音采集暂时中断，正在重新连接")
                    continue
                if self.device_id is None:
                    capture_error_since = capture_error_since or monotonic()
                    if monotonic() - capture_error_since < self.RECONNECT_SECONDS:
                        stop_event.wait(0.1)
                        continue
                if isinstance(exc, AudioDeviceError):
                    raise
                raise AudioDeviceError("output audio capture failed") from exc

    def _capture_selected(self, backend, selected: OutputDevice, stop_event: Event) -> Iterator[np.ndarray]:
        microphone = backend.get_microphone(id=selected.id, include_loopback=True)
        if microphone is None:
            raise AudioDeviceError("output device has no loopback input")
        self.active_device = selected
        pending = np.empty(0, dtype=np.float32)
        frames_since_check = 0
        with microphone.recorder(samplerate=self.sample_rate, blocksize=self.frame_samples) as recorder:
            self.capture_generation += 1
            while not stop_event.is_set():
                raw = np.asarray(recorder.record(numframes=self.frame_samples), dtype=np.float32)
                mono = _downmix(raw)
                if not len(mono):
                    continue
                pending = np.concatenate((pending, mono))
                while len(pending) >= self.frame_samples:
                    frame = pending[: self.frame_samples].copy()
                    pending = pending[self.frame_samples :]
                    yield frame
                    if self.device_id is None:
                        frames_since_check += 1
                        if frames_since_check >= self.device_check_frames:
                            frames_since_check = 0
                            try:
                                current = next(
                                    (device for device in list_output_devices(backend) if device.is_default), None
                                )
                            except AudioDeviceError:
                                return
                            if current is None or current.id != selected.id:
                                return


class SileroSpeechDetector:
    """Classify the newest frame using Silero VAD with recent audio context."""

    def __init__(self, *, sample_rate: int = 16000, frame_samples: int = 1600) -> None:
        if sample_rate != 16000 or frame_samples != 1600:
            raise ValueError("Silero detector needs 16 kHz, 100 ms frames")
        from faster_whisper.vad import get_vad_model

        self.model = get_vad_model()
        self._recent: deque[np.ndarray] = deque(maxlen=10)

    def __call__(self, frame: np.ndarray) -> bool:
        self._recent.append(frame)
        audio = np.concatenate(self._recent)
        if float(np.max(np.abs(audio))) < 0.003:
            return False
        audio = np.pad(audio, (0, (-len(audio)) % 512)).astype(np.float32)
        scores = self.model(audio).reshape(-1)
        return bool(np.max(scores[-5:]) >= 0.35)

    def reset(self) -> None:
        self._recent.clear()


class SpeechSegmenter:
    """A conservative energy gate that bounds inference clip lengths."""

    def __init__(
        self,
        *,
        sample_rate: int,
        frame_samples: int,
        threshold: float = 0.005,
        min_speech_seconds: float = 0.2,
        silence_seconds: float = 0.4,
        max_seconds: float = 7.0,
        voice_detector: Callable[[np.ndarray], bool] | None = None,
    ) -> None:
        if sample_rate <= 0 or frame_samples <= 0 or threshold < 0:
            raise ValueError("invalid segmenter audio parameters")
        if min_speech_seconds <= 0 or silence_seconds <= 0 or max_seconds <= 0:
            raise ValueError("segment durations must be positive")
        frames_per_second = sample_rate / frame_samples
        self.frame_samples = frame_samples
        self.threshold = threshold
        self.min_voice_frames = ceil(min_speech_seconds * frames_per_second)
        self.quiet_frames_needed = ceil(silence_seconds * frames_per_second)
        self.max_frames = ceil(max_seconds * frames_per_second)
        self.voice_detector = voice_detector
        self._preroll: deque[np.ndarray] = deque(maxlen=2)
        self._frames: list[np.ndarray] = []
        self._voice_frames = 0
        self._quiet_frames = 0

    @property
    def active_frames(self) -> int:
        return len(self._frames)

    def snapshot(self) -> np.ndarray | None:
        """Copy the current utterance without consuming its final audio."""
        if self._voice_frames < self.min_voice_frames:
            return None
        return np.concatenate(self._frames)

    def push(self, frame: np.ndarray) -> list[np.ndarray]:
        if frame.ndim != 1 or len(frame) != self.frame_samples:
            raise ValueError("frame must be a fixed-size mono array")
        frame = np.asarray(frame, dtype=np.float32).copy()
        voiced = (
            self.voice_detector(frame)
            if self.voice_detector is not None
            else float(np.sqrt(np.mean(np.square(frame, dtype=np.float32)))) >= self.threshold
        )
        if not self._frames:
            if not voiced:
                self._preroll.append(frame)
                return []
            self._frames = [*self._preroll, frame]
            self._preroll.clear()
            self._voice_frames = 1
            return []

        self._frames.append(frame)
        if voiced:
            self._voice_frames += 1
            self._quiet_frames = 0
        else:
            self._quiet_frames += 1
        if len(self._frames) >= self.max_frames or self._quiet_frames >= self.quiet_frames_needed:
            clip = self.flush()
            return [clip] if clip is not None else []
        return []

    def flush(self) -> np.ndarray | None:
        clip = np.concatenate(self._frames) if self._voice_frames >= self.min_voice_frames else None
        self._frames = []
        self._voice_frames = 0
        self._quiet_frames = 0
        return clip
