"""Windows output loopback capture and bounded audio segmentation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import ceil
from threading import Event
from typing import Callable, Iterator

import numpy as np


class AudioDeviceError(RuntimeError):
    """The selected playback device cannot currently be captured."""


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
