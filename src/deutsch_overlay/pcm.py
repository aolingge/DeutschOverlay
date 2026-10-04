"""PCM intake: decode, downmix, resample, and place audio on a media timeline.

The bridge receives compressed-in-transit PCM from a browser tab. Turning that
into something the recognizer can decode is three separable problems, each
with its own failure mode that must be visible rather than silent:

* base64/int16 decoding - a malformed payload is a client bug;
* resampling - preserve filter state between packets and flush the delayed
  tail, so conversion does not distort audio or shift later captions;
* sample accounting - a packet that arrives out of order is dropped and
  reported as a gap, because quietly gluing non-adjacent audio together
  produces captions that look right and are timed wrong.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from math import ceil

import numpy as np

from .browser_protocol import ErrorCode, ProtocolError

RECOGNIZER_SAMPLE_RATE = 16000
_S16_SCALE = 1.0 / 32768.0


def decode_pcm_s16le(payload: str) -> np.ndarray:
    """Base64 text -> interleaved int16 numpy array. Raises ProtocolError."""
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProtocolError(ErrorCode.BAD_REQUEST, f"pcm is not valid base64: {exc}") from exc
    if len(raw) % 2:
        raise ProtocolError(
            ErrorCode.BAD_REQUEST,
            f"pcm has an odd byte count ({len(raw)}); 16-bit samples need two bytes each",
        )
    return np.frombuffer(raw, dtype="<i2")


def downmix(samples: np.ndarray, channels: int) -> np.ndarray:
    """Interleaved int16 -> mono float32 in [-1, 1)."""
    if channels == 1:
        return samples.astype(np.float32) * _S16_SCALE
    if channels != 2:
        raise ProtocolError(ErrorCode.BAD_REQUEST, f"unsupported channel count: {channels}")
    if samples.size % 2:
        raise ProtocolError(
            ErrorCode.BAD_REQUEST, "stereo pcm carries an odd number of samples"
        )
    pairs = samples.reshape(-1, 2).astype(np.float32)
    return pairs.mean(axis=1) * _S16_SCALE


def decode_packet(payload: str, channels: int) -> np.ndarray:
    """One packet payload -> mono float32 at its declared rate."""
    return downmix(decode_pcm_s16le(payload), channels)


class Resampler:
    """Stateful FFmpeg resampling with source-aligned output coordinates.

    The filter delays delivery while waiting for neighbouring input samples.
    That delay is not added to media timestamps. Flush releases the tail once
    the submitted stream is complete; reset starts a separate timeline.
    """

    def __init__(self, in_rate: int, out_rate: int = RECOGNIZER_SAMPLE_RATE) -> None:
        if in_rate <= 0 or out_rate <= 0:
            raise ValueError("sample rates must be positive")
        self.in_rate = int(in_rate)
        self.out_rate = int(out_rate)
        self.identity = self.in_rate == self.out_rate
        self.reset()

    def expected_output_total(self, input_total: int) -> int:
        return ceil(int(input_total) * self.out_rate / self.in_rate)

    def process(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if self._finished:
            raise ValueError("reset resampler before submitting a new stream")
        if not np.isfinite(block).all():
            raise ValueError("audio contains non-finite samples")
        if not block.size:
            return np.zeros(0, dtype=np.float32)
        self.total_input += int(block.size)
        if self.identity:
            self.total_output += int(block.size)
            return block.copy()
        import av

        frame = av.AudioFrame.from_ndarray(block[None, :], format="fltp", layout="mono")
        frame.sample_rate = self.in_rate
        return self._collect(self._filter.resample(frame))

    def _collect(self, frames) -> np.ndarray:
        chunks = [frame.to_ndarray().reshape(-1) for frame in frames]
        output = np.concatenate(chunks).astype(np.float32, copy=False) if chunks else np.zeros(0, dtype=np.float32)
        self.total_output += int(output.size)
        return output

    def flush(self) -> np.ndarray:
        if self._finished:
            return np.zeros(0, dtype=np.float32)
        self._finished = True
        if self.identity or not self.total_input:
            return np.zeros(0, dtype=np.float32)
        return self._collect(self._filter.resample(None))

    def reset(self) -> None:
        self.total_input = 0
        self.total_output = 0
        self._finished = False
        self._filter = None
        if not self.identity:
            import av

            self._filter = av.AudioResampler(format="fltp", layout="mono", rate=self.out_rate)


@dataclass(frozen=True, slots=True)
class TimelineAnchor:
    """The browser's own statement of what a moment in the audio stream was.

    ``audio_start_ms`` is the media position of the first sample of the
    submitted stream. It exists so captions can be timed from the video's
    clock instead of from the moment recognition happened to finish.

    ``playback_rate`` is recorded for diagnostics only. The extension already
    converts the captured clock into *media* milliseconds before anchoring, so
    applying the rate again here would scale a value that is no longer in
    wall-clock units.
    """

    sample_index: int
    audio_start_ms: int
    observed_at: float
    timeline_epoch: int = 0
    playback_rate: float | None = None
    paused: bool = False

    @property
    def media_offset_ms(self) -> float:
        return float(self.audio_start_ms)


@dataclass(slots=True)
class AudioTimeline:
    """Maps submitted-sample positions onto media time using browser anchors."""

    rate: int = RECOGNIZER_SAMPLE_RATE
    anchors: list[TimelineAnchor] = field(default_factory=list)

    def add_anchor(self, anchor: TimelineAnchor) -> None:
        self.anchors.append(anchor)
        if len(self.anchors) > 64:
            del self.anchors[:-64]

    def rebase(self, anchor: TimelineAnchor) -> None:
        """A new timeline epoch invalidates every earlier anchor."""
        self.anchors = [anchor]

    def media_ms(self, sample_index: int) -> float:
        """Media position of a submitted sample, or ``nan`` without an anchor.

        A sample earlier than the earliest anchor clamps to that anchor instead
        of extrapolating backwards: nothing observed so far says the audio before
        the anchor belonged to the same timeline, and a wrong earlier time is
        worse for a caption than a slightly late one.
        """
        anchor = self._anchor_for(sample_index)
        if anchor is None:
            return float("nan")
        if sample_index <= anchor.sample_index:
            return float(anchor.audio_start_ms)
        return anchor.audio_start_ms + (sample_index - anchor.sample_index) * 1000.0 / self.rate

    def _anchor_for(self, sample_index: int) -> TimelineAnchor | None:
        if not self.anchors:
            return None
        best: TimelineAnchor | None = None
        for anchor in self.anchors:
            if anchor.sample_index <= sample_index:
                if best is None or anchor.sample_index > best.sample_index:
                    best = anchor
        return best if best is not None else self.anchors[0]

    @property
    def has_anchor(self) -> bool:
        return bool(self.anchors)
