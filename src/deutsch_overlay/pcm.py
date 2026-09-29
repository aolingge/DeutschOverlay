"""PCM intake: decode, downmix, resample, and place audio on a media timeline.

The bridge receives compressed-in-transit PCM from a browser tab. Turning that
into something the recognizer can decode is three separable problems, each
with its own failure mode that must be visible rather than silent:

* base64/int16 decoding - a malformed payload is a client bug;
* resampling - a wrong length here shifts every later caption, so the
  converter guarantees a deterministic output count instead of trusting the
  filter to happen to line up;
* sample accounting - a packet that arrives out of order is dropped and
  reported as a gap, because quietly gluing non-adjacent audio together
  produces captions that look right and are timed wrong.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from math import ceil, gcd

import numpy as np

from .browser_protocol import ErrorCode, ProtocolError

RECOGNIZER_SAMPLE_RATE = 16000
_S16_SCALE = 1.0 / 32768.0
# Per-phase tap budget for the resampling polyphase bank. It bounds both the
# filter's memory and the audio that must arrive before the first output can be
# produced - about 16 ms at 16 kHz - which keeps capture latency predictable.
MAX_TAPS_PER_PHASE = 256


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
    """Streaming band-limited resampler with an exact, predictable output count.

    Output sample ``n`` corresponds to input position ``n * in_rate / out_rate``.
    Because that mapping is arithmetic, a caller can tell exactly where each
    output sample came from, and the number of output samples for ``N`` input
    samples is always ``ceil(N * out_rate / in_rate)`` - no drift accumulates
    across chunk boundaries.
    """

    def __init__(self, in_rate: int, out_rate: int = RECOGNIZER_SAMPLE_RATE) -> None:
        if in_rate <= 0 or out_rate <= 0:
            raise ValueError("sample rates must be positive")
        self.in_rate = int(in_rate)
        self.out_rate = int(out_rate)
        factor = gcd(self.in_rate, self.out_rate)
        self.up = self.out_rate // factor
        self.down = self.in_rate // factor
        self.identity = self.in_rate == self.out_rate
        # A polyphase bank with a bounded number of taps per phase. The bank holds
        # one output phase per distinct fractional input position, so a phase's
        # taps sit `banks` samples apart in the input, and each phase covers
        # `down / banks` input samples. Bounding the taps per phase bounds both the
        # filter's memory and how much audio must arrive before the first output
        # can be produced - a few tens of milliseconds at most.
        divisor = gcd(self.up, self.down)
        banks = max(1, self.up // divisor)
        per_phase = max(1, self.down // banks)
        half = min(MAX_TAPS_PER_PHASE // 2, max(per_phase // 2, 1))
        taps = 2 * half + 1
        self._half = half
        self._centre = half
        positions = np.arange(-half, half + 1, dtype=np.float64)
        # This filter always downsamples in aggregate; cut just below the output
        # Nyquist so nothing above it folds back into the speech band.
        cutoff = 0.5 / per_phase * 0.94
        arguments = 2.0 * np.pi * cutoff * positions
        coefficients = np.where(
            np.isclose(arguments, 0.0), 1.0, np.sin(arguments) / np.where(arguments == 0.0, 1.0, arguments)
        )
        window = np.kaiser(taps, 6.0) if taps > 1 else np.ones(1)
        coefficients = coefficients * window
        coefficients /= np.sum(coefficients)
        self._coefficients = coefficients.astype(np.float32)
        self._phases = []
        for phase in range(banks):
            row = np.arange(phase, taps, banks, dtype=np.int64)
            if row.size == 0:
                row = np.array([min(phase, taps - 1)], dtype=np.int64)
            bank = coefficients[row].astype(np.float32)
            self._phases.append(bank / np.sum(bank))
        self._span = taps
        self._buf = np.zeros(0, dtype=np.float32)
        self.total_input = 0
        self.total_output = 0
        # Absolute input index of the first sample kept in _tail, and the output
        # index whose window starts there; together they let any window be located
        # in the buffer by arithmetic instead of bookkeeping at every block edge.
        self._start_index = 0
        self._next_output_base = 0
        self._emitted = 0
        self._primed = False

    @property
    def span(self) -> int:
        return self._span

    def output_index_for_input(self, input_index: int) -> int:
        return (int(input_index) * self.up) // self.down

    def input_position_for_output(self, output_index: int) -> float:
        return int(output_index) * self.down / self.up

    def expected_output_total(self, input_total: int) -> int:
        return (int(input_total) * self.up + self.down - 1) // self.down

    def _window_start(self, output_index: int) -> int:
        """Absolute input index of the first tap of ``output_index``'s window.

        The bank is centred, so output ``n`` reads samples centred on input
        ``n * down / up`` and begins ``half`` samples before that. ``span`` counts
        from here, so window and span always describe the same samples.
        """
        return (output_index * self.down) // self.up - self._half

    def input_needed_for(self, output_index: int) -> int:
        """Smallest input count that can produce ``output_index`` without guessing.

        Emitting an output before its last tap's sample exists would convolve
        against audio that has not arrived, and the result would then depend on
        how the caller happened to cut the stream into blocks.
        """
        start = self._window_start(output_index)
        return start + self._span

    def safe_emit_count(self, available: int, first: int | None = None) -> int:
        """How many queued outputs the input available so far can support.

        ``available`` is the stream's total input count, not the buffer length,
        and ``first`` is the first output index still owed (defaults to the one
        the live buffer is positioned at).
        """
        if self.identity:
            return max(0, int(available) - self._emitted)
        base = self._next_output_base if first is None else first
        total = max(0, int(available) - base)
        if total <= 0:
            return 0
        # The window for output n starts at floor(n * down / up) - 2 * half in
        # buffer coordinates, so the crossing point can be estimated in one step
        # and then checked exactly on either side.
        slack = 2 * self._half + self._start_index
        estimate = ((int(available) - slack) * self.up) // self.down + 1
        index = max(0, min(total, estimate))
        while index > 0 and self.input_needed_for(base + index - 1) > available:
            index -= 1
        while index < total and self.input_needed_for(base + index) <= available:
            index += 1
        return index

    def process(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if block.size == 0:
            return np.zeros(0, dtype=np.float32)
        if not self._primed:
            self._prime()
        self.total_input += int(block.size)
        if self.identity:
            self.total_output += int(block.size)
            return block.copy()
        target = self.expected_output_total(self.total_input)
        # Hold back outputs whose filter window still reaches past the submitted
        # audio: producing them now would read the buffer's unwritten edge and
        # disagree with the same audio submitted in one piece. flush() releases
        # whatever remains once the stream is known to be over.
        count = min(target - self._emitted, self.safe_emit_count(self.total_input))
        # Keep everything from the earliest pending window's first tap onwards,
        # then append this block: _buf[0] is absolute sample _start_index and the
        # buffer still ends on the newest submitted sample.
        begin = self._window_start(self._next_output_base)
        self._buf = self._buf[begin - self._start_index :].copy()
        self._start_index = begin
        self._buf = np.concatenate((self._buf, block))
        if self._start_index + self._buf.size > self.total_input:
            self._buf = self._buf[: self.total_input - self._start_index]
        if count <= 0:
            return np.zeros(0, dtype=np.float32)
        output = self._convolve(self._next_output_base, count)
        self._emitted += count
        self._next_output_base += count
        self.total_output = self._emitted
        return output

    def _prime(self) -> None:
        """Start the history before sample 0 so the filter's leading edge is zeros."""
        self._primed = True
        if self.identity or self._span <= 1:
            return
        self._next_output_base = 0
        self._start_index = self._window_start(0)
        # Leading zeros stand in for the samples before the stream began.
        self._buf = np.zeros(-self._start_index, dtype=np.float32)

    def _convolve(self, first_output: int, count: int) -> np.ndarray:
        positions = (
            np.arange(first_output, first_output + count, dtype=np.int64) * self.down
        )
        phases = (positions % self.up).astype(np.int64)
        starts = positions // self.up - self._half - self._start_index
        out = np.zeros(count, dtype=np.float32)
        for phase in range(self.up):
            selection = np.nonzero(phases == phase)[0]
            if selection.size == 0:
                continue
            offsets = starts[selection]
            taps = self._phases[phase]
            width = taps.size
            index = offsets[:, None] + np.arange(width, dtype=np.int64)[None, :]
            # 0, not -1: a negative floor would wrap to the buffer's end and
            # silently mix future samples into the first outputs.
            np.clip(index, 0, self._buf.size - 1, out=index)
            out[selection] = self._buf[index] @ taps
        return out

    def flush(self) -> np.ndarray:
        """Emit the samples the filter still owes, then behave like a new stream."""
        if self.identity:
            return np.zeros(0, dtype=np.float32)
        target = self.expected_output_total(self.total_input)
        remaining = target - self._emitted
        if remaining <= 0:
            return np.zeros(0, dtype=np.float32)
        output = self._convolve(self._next_output_base, remaining)
        self._next_output_base += remaining
        self._emitted = target
        self.total_output = target
        return output

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self.total_input = 0
        self.total_output = 0
        self._start_index = 0
        self._next_output_base = 0
        self._emitted = 0
        self._primed = False


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
