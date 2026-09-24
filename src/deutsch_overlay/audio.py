"""Windows output loopback capture and bounded audio segmentation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import ceil
from threading import Event
from typing import Iterator

import numpy as np


class AudioDeviceError(RuntimeError):
    """The selected playback device cannot currently be captured."""


@dataclass(frozen=True, slots=True)
class OutputDevice:
    id: str
    name: str
    is_default: bool


def _soundcard():
    import soundcard

    return soundcard


def list_output_devices(backend=None) -> list[OutputDevice]:
    backend = backend or _soundcard()
    try:
        default = backend.default_speaker()
        default_id = default.id if default is not None else None
        return [
            OutputDevice(str(speaker.id), speaker.name, speaker.id == default_id)
            for speaker in backend.all_speakers()
        ]
    except (OSError, RuntimeError) as exc:
        raise AudioDeviceError("output devices could not be listed") from exc


class LoopbackSource:
    """Yield fixed-size mono float32 frames from one playback endpoint."""

    def __init__(
        self,
        device_id: str | None = None,
        *,
        backend=None,
        sample_rate: int = 16000,
        frame_samples: int = 1600,
    ) -> None:
        if sample_rate <= 0 or frame_samples <= 0:
            raise ValueError("sample_rate and frame_samples must be positive")
        self.device_id = device_id
        self.backend = backend
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples

    def frames(self, stop_event: Event) -> Iterator[np.ndarray]:
        backend = self.backend or _soundcard()
        devices = list_output_devices(backend)
        selected = next(
            (device for device in devices if device.id == self.device_id), None
        ) if self.device_id else next((device for device in devices if device.is_default), None)
        if selected is None:
            raise AudioDeviceError("selected output device is unavailable")
        try:
            microphone = backend.get_microphone(id=selected.id, include_loopback=True)
            if microphone is None:
                raise AudioDeviceError("output device has no loopback input")
            pending = np.empty(0, dtype=np.float32)
            with microphone.recorder(samplerate=self.sample_rate, blocksize=self.frame_samples) as recorder:
                while not stop_event.is_set():
                    raw = np.asarray(recorder.record(numframes=self.frame_samples), dtype=np.float32)
                    if raw.ndim == 2:
                        mono = raw.mean(axis=1, dtype=np.float32)
                    elif raw.ndim == 1:
                        mono = raw
                    else:
                        raise AudioDeviceError("capture returned an unsupported audio format")
                    if not len(mono):
                        continue
                    pending = np.concatenate((pending, mono))
                    while len(pending) >= self.frame_samples:
                        frame = pending[: self.frame_samples].copy()
                        pending = pending[self.frame_samples :]
                        yield frame
        except AudioDeviceError:
            raise
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            raise AudioDeviceError("output audio capture failed") from exc


class SpeechSegmenter:
    """A conservative energy gate that bounds inference clip lengths."""

    def __init__(
        self,
        *,
        sample_rate: int,
        frame_samples: int,
        threshold: float = 0.015,
        min_speech_seconds: float = 0.2,
        silence_seconds: float = 0.4,
        max_seconds: float = 7.0,
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
        self._preroll: deque[np.ndarray] = deque(maxlen=2)
        self._frames: list[np.ndarray] = []
        self._voice_frames = 0
        self._quiet_frames = 0

    def push(self, frame: np.ndarray) -> list[np.ndarray]:
        if frame.ndim != 1 or len(frame) != self.frame_samples:
            raise ValueError("frame must be a fixed-size mono array")
        frame = np.asarray(frame, dtype=np.float32).copy()
        voiced = float(np.sqrt(np.mean(np.square(frame, dtype=np.float32)))) >= self.threshold
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
