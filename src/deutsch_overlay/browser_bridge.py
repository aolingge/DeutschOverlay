"""Local ASR bridge: sessions, caption gates, and per-tab recognition queues.

The bridge is the only part of the desktop app that a browser talks to. It
exists so the page never needs to know how recognition works: the extension
declares what it sees, submits PCM, and polls for timed captions.

Three rules are enforced here rather than in the extension, because the
extension cannot be trusted to police itself:

1. A video that reports a usable caption track is never recognized. Reporting
   ``unknown`` is treated as "has captions" too - recognizing on a guess burns
   the local model on videos that already had subtitles.
2. Media time comes from the anchor the extension published. Recognition
   finishing late must not move a caption, so nothing here reads a wall clock
   for timing.
3. Audio that does not join up with what came before is treated as new stream
   (a seek), never spliced onto the previous one, so a gap cannot silently
   shift every later caption.
"""

from __future__ import annotations

import base64
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .audio import PositionedSpeechSegmenter, SpeechSpan
from .browser_protocol import (
    AudioPacket,
    AudioRequest,
    AudioResponse,
    BridgeStatus,
    CaptionAvailability,
    ErrorCode,
    ProtocolError,
    SegmentView,
    SessionRequest,
    SessionResponse,
    Status,
    SourceKind,
    TranscriptQuery,
    TranscriptResponse,
    PROTOCOL_VERSION,
)
from .pcm import (
    AudioTimeline,
    RECOGNIZER_SAMPLE_RATE,
    Resampler,
    TimelineAnchor,
    decode_packet,
)
from .transcript import TranscriptResult, TranscriptSegment

GAP_TOLERANCE_SECONDS = 0.5
MAX_SNAPSHOT_REVISIONS = 64


def _now() -> float:
    return time.monotonic()


def new_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass(slots=True)
class StoredSegment:
    """One published caption revision plus the source data it came from."""

    view: SegmentView
    source: TranscriptSegment
    translation_attempted: bool = False


@dataclass(slots=True)
class ClipJob:
    span: SpeechSpan
    provisional: bool
    language: str
    epoch: int = 0


@dataclass(slots=True)
class BridgeSession:
    """One browser tab's stream: its declared facts, audio, and captions."""

    session_id: str
    request: SessionRequest
    recognize: bool
    reason: str
    engine: str
    warning: str = ""
    created_at: float = field(default_factory=_now)
    last_touch: float = field(default_factory=_now)

    # audio accounting
    resampler: Resampler | None = None
    timeline: AudioTimeline = field(default_factory=AudioTimeline)
    next_sample: int = 0  # contiguous position: everything below it was accepted
    last_sequence: int = -1
    gap_samples: int = 0
    accepted_packets: int = 0
    accepted_samples: int = 0
    stream_complete: bool = False
    # The epoch the page last declared, or ``None`` when it has declared none.
    # The page's own numbering cannot be used to tell whether a stream changed,
    # because a page is free to declare no epoch at all.
    declared_epoch: int | None = None
    # A counter of this session's streams: 0 until a restart creates a new one.
    # It labels which stream a caption belongs to; it is not the page's number.
    timeline_epoch: int = 0
    language: str = "auto"
    language_evidence: dict[str, int] = field(default_factory=dict)
    language_confirmed: bool = False
    last_language_clip: int | None = None

    # recognition
    jobs: list[ClipJob] = field(default_factory=list)
    job_lock: threading.Lock = field(default_factory=threading.Lock)
    worker: threading.Thread | None = None
    idle: threading.Event = field(default_factory=threading.Event)
    wake: threading.Event = field(default_factory=threading.Event)
    stopped: bool = False
    busy: bool = False
    carry: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    frame_cursor: int = 0
    restart_origin: int = 0
    raw_since_restart: int = 0
    _segmenter: Any = None

    # results
    revisions: list[StoredSegment] = field(default_factory=list)
    revision: int = 0
    store_lock: threading.Lock = field(default_factory=threading.Lock)
    translation_backend: str = ""
    recognized_segments: int = 0
    translation_failures: int = 0

    def touch(self) -> None:
        self.last_touch = _now()

    @property
    def sample_rate(self) -> int:
        return RECOGNIZER_SAMPLE_RATE

    @property
    def stream_position(self) -> int:
        """Position in the page's sample counter that the next packet must continue.

        The page numbers its samples from wherever capture began, but a seek or
        a new timeline epoch restarts that numbering while the viewer's media
        time jumps, so the origin of the current stream is kept in
        ``restart_origin`` and the raw samples accepted since are added in the
        page's own units. The resampled output grows at 16 kHz whatever the page
        sends, so counting by it would invent gaps in any 48 kHz stream.
        """
        return self.restart_origin + self.raw_since_restart

    def media_ms(self, sample_index: int) -> float:
        return self.timeline.media_ms(sample_index)

    def status(self) -> str:
        with self.store_lock:
            if not self.recognize:
                return Status.EMPTY
            if not self.revisions:
                return Status.RECOGNIZING
            latest = self.revisions[-1].view
            if latest.provisional:
                return Status.RECOGNIZING
            if (
                latest.source_language not in ("de", "")
                and not latest.translation_backend
                and not latest.translation_failed
            ):
                return Status.TRANSLATING
            if self.busy or self.jobs or not self.stream_complete:
                return Status.RECOGNIZING
            return Status.READY

    def segments_since(self, since_revision: int) -> tuple[tuple[SegmentView, ...], int]:
        """Latest revision of every segment, for a client at ``since_revision``."""
        with self.store_lock:
            current = self.revision
            grouped: dict[str, SegmentView] = {}
            order: list[str] = []
            for stored in self.revisions:
                if stored.view.revision <= since_revision:
                    continue
                key = stored.view.segment_id
                if key not in grouped:
                    order.append(key)
                grouped[key] = stored.view
            return tuple(grouped[key] for key in order), current

    def all_segments(self) -> tuple[SegmentView, ...]:
        with self.store_lock:
            grouped: dict[str, SegmentView] = {}
            for stored in self.revisions:
                grouped[stored.view.segment_id] = stored.view
            return tuple(grouped.values())

    def progress_ms(self) -> int:
        if self.timeline.has_anchor:
            value = self.media_ms(self.next_sample - 1)
            if value == value:  # not nan
                return max(0, int(round(value)))
        return int(self.next_sample * 1000 / self.sample_rate)


class AsrBridgeService:
    """Transport-independent bridge logic. The HTTP layer only marshals JSON."""

    def __init__(
        self,
        engine,
        *,
        segmenter_factory: Callable[..., Any] | None = None,
        token: str | None = None,
        max_sessions: int = 4,
        token_path=None,
        frame_samples: int = 1600,
    ) -> None:
        self.engine = engine
        self.token = token or new_token()
        self.token_path = token_path
        self.max_sessions = max_sessions
        self._frame_samples = frame_samples
        self.sessions: dict[str, BridgeSession] = {}
        self.status = BridgeStatus()
        self._lock = threading.Lock()
        self._segmenter_factory = segmenter_factory or _default_segmenter_factory
        self.prepare_lock = threading.Lock()
        self.prepared = False
        self.prepare_error = ""

    # ------------------------------------------------------------------ health

    @property
    def engine_name(self) -> str:
        return getattr(self.engine, "name", "") or type(self.engine).__name__

    def health(self) -> dict[str, Any]:
        with self._lock:
            sessions = len(self.sessions)
        return {
            "ok": True,
            "protocolVersion": PROTOCOL_VERSION,
            "service": "deutsch-overlay-bridge",
            "modelReady": self.prepared,
            "engine": self.engine_name,
            "languages": ["de", "en", "zh"],
            "warning": self.prepare_error or getattr(self.engine, "warning", "") or "",
            "sessions": sessions,
        }

    def prepare(self, *, blocking: bool = False) -> None:
        """Load models once. Never called implicitly by ``health``."""
        with self.prepare_lock:
            if self.prepared or self.prepare_error:
                return
            self.prepared = True
            try:
                self.engine.prepare()
            except Exception as exc:  # surfaced through /v1/health instead of crashing
                self.prepared = False
                self.prepare_error = f"模型准备失败：{type(exc).__name__}: {exc}"

    # ----------------------------------------------------------------- sessions

    def create_session(self, request: SessionRequest) -> SessionResponse:
        availability = request.caption_availability
        if availability == CaptionAvailability.PRESENT:
            recognize, reason = False, "captions_present"
        elif availability == CaptionAvailability.UNKNOWN:
            recognize, reason = False, "captions_unknown"
        elif request.has_audio_track is False:
            recognize, reason = False, "no_audio_track"
        else:
            recognize, reason = True, "no_captions"

        if recognize:
            self.prepare(blocking=True)
            if self.prepare_error:
                raise ProtocolError(ErrorCode.ENGINE_UNAVAILABLE, self.prepare_error)
        with self._lock:
            expired = [key for key, value in self.sessions.items() if value.stopped]
            for key in expired:
                del self.sessions[key]
            if len(self.sessions) >= self.max_sessions:
                raise ProtocolError(
                    ErrorCode.ENGINE_BUSY,
                    f"本机桥接最多同时处理 {self.max_sessions} 个页面，请先关闭其他标签页的字幕",
                )
            session_id = uuid.uuid4().hex
            session = BridgeSession(
                session_id=session_id,
                request=request,
                recognize=recognize,
                reason=reason,
                engine=self.engine_name,
                warning=getattr(self.engine, "warning", "") or "",
                language=request.source_language,
                # A seek that declared no epoch is still the same undeclared
                # stream, so the session must not invent a number for it: only a
                # declared epoch names a new stream.
                declared_epoch=request.timeline_epoch,
                timeline_epoch=(
                    request.timeline_epoch if request.timeline_epoch is not None else 0
                ),
            )
            self.sessions[session_id] = session
            self.status.sessions = dict(self.sessions)
            if recognize:
                self._start_worker(session)
        if recognize and request.audio_start_ms is not None:
            session.timeline.rebase(
                TimelineAnchor(
                    sample_index=0,
                    audio_start_ms=int(request.audio_start_ms),
                    observed_at=session.created_at,
                    timeline_epoch=request.timeline_epoch,
                    playback_rate=request.playback_rate,
                )
            )
        return SessionResponse(
            session_id=session_id,
            protocol_version=PROTOCOL_VERSION,
            engine=self.engine_name,
            source_language=session.language,
            recognize=recognize,
            reason=reason,
            message=self._gate_message(reason),
            accepted_from_sample=0,
        )

    @staticmethod
    def _gate_message(reason: str) -> str:
        return {
            "no_captions": "未检测到可用字幕，开始本机识别",
            "captions_present": "该视频已有字幕，本机识别不会启动",
            "captions_unknown": "无法确认字幕状态，未启动本机识别",
            "no_audio_track": "该视频没有音轨，无法识别",
        }.get(reason, reason)

    def get(self, session_id: str) -> BridgeSession:
        with self._lock:
            session = self.sessions.get(session_id)
        if session is None:
            raise ProtocolError(ErrorCode.NOT_FOUND, f"unknown session: {session_id}")
        return session

    def close_session(self, session_id: str) -> int:
        session = self.get(session_id)
        self._stop_worker(session)
        with self._lock:
            self.sessions.pop(session_id, None)
            self.status.sessions = dict(self.sessions)
        return len(session.revisions)

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
            self.status.sessions = {}
        for session in sessions:
            self._stop_worker(session)

    # -------------------------------------------------------------- audio input

    def feed(self, session: BridgeSession, request: AudioRequest) -> AudioResponse:
        session.touch()
        if not session.recognize:
            raise ProtocolError(
                ErrorCode.CAPTIONS_PRESENT,
                f"本会话未开启识别（{session.reason}）",
            )
        accepted_from = session.next_sample
        for packet in sorted(request.packets, key=lambda item: item.sample_index):
            if session.stream_complete:
                # A finished stream is over. Only a packet that opens a new one
                # is acceptable: a seek or a new timeline epoch means the viewer
                # started somewhere else, and refusing it would leave them
                # without captions until the page reloads.
                if packet.timeline_epoch is None or packet.timeline_epoch == session.timeline_epoch:
                    raise ProtocolError(
                        ErrorCode.CONFLICT, "stream already marked complete"
                    )
            if packet.sequence <= session.last_sequence:
                continue  # duplicate or out-of-order retry: already counted
            self._ingest(session, packet)
            session.last_sequence = max(session.last_sequence, packet.sequence)
            session.accepted_packets += 1
        if request.stream_complete:
            session.stream_complete = True
            if session.resampler is not None:
                tail = session.resampler.flush()
                self._push_audio(session, tail)
                session.next_sample += int(tail.size)
                session.accepted_samples += int(tail.size)
            if session.carry.size:
                # Only the last frame is padded for VAD. Clip coordinates and
                # inference samples are trimmed to the real submitted duration.
                padding = self._frame_samples - session.carry.size
                self._push_audio(session, np.zeros(padding, dtype=np.float32), real_end=session.next_sample)
            span = self._flush_segmenter(session)
            if span is not None:
                self._enqueue(session, span, provisional=False)
        elif request.flush:
            span = self._snapshot(session)
            if span is not None:
                self._enqueue(session, span, provisional=False)
        self._wake(session)
        accepted_to = session.next_sample
        segments, revision = session.segments_since(0)
        return AudioResponse(
            accepted_samples=session.accepted_samples,
            accepted_range=(accepted_from, accepted_to),
            buffered_samples=self._buffered_samples(session),
            gap_samples=session.gap_samples,
            segments=tuple(item.to_dict() for item in segments),
            language=session.language,
            stream_complete=session.stream_complete,
            engine_warning=session.warning,
        )

    def _buffered_samples(self, session: BridgeSession) -> int:
        if session._segmenter is None:
            return 0
        return int(session._segmenter.buffered_samples)

    def _ingest(self, session: BridgeSession, packet: AudioPacket) -> None:
        if packet.encoding != "pcm_s16le":
            raise ProtocolError(
                ErrorCode.BAD_REQUEST, f"unsupported encoding: {packet.encoding}"
            )
        samples = decode_packet(packet.pcm_base64, packet.channels)
        epoch_changed = (
            packet.timeline_epoch is not None
            and packet.timeline_epoch != session.timeline_epoch
        )
        # ``next_sample`` is the position the stream has buffered: it is what the
        # next packet's ``sampleIndex`` is measured against, so it is what says
        # whether audio was skipped.
        expected = session.stream_position
        seek = False
        gap = 0
        if packet.sample_index > expected:
            gap = packet.sample_index - expected
            seek = gap > int(GAP_TOLERANCE_SECONDS * packet.sample_rate)
            session.gap_samples += gap if not seek else 0
        # A seek and a new timeline epoch are the same event from the bridge's
        # point of view: the sample counter is no longer continuous, so the
        # stream restarts at 0 and the anchor becomes ``sample 0 = this media
        # time``. Splicing the old coordinates onto the new media position
        # would time every later caption by the size of the jump.
        restart = seek or epoch_changed
        if restart and packet.sample_index > expected:
            # The jump itself is not part of the new stream: measuring the
            # packet's span from the position it left behind would count the
            # whole seek as audio the page will still send.
            expected = packet.sample_index
        if not restart and packet.sample_index < expected:
            # Overlapping retransmission: drop the audio already buffered. This
            # must not run after a restart, when the old position no longer
            # describes where the new stream begins.
            skip = expected - packet.sample_index
            if skip >= samples.size:
                return
            samples = samples[skip:]

        if restart and packet.timeline_epoch is not None:
            session.declared_epoch = packet.timeline_epoch
        anchor_ms = packet.audio_start_ms
        if anchor_ms is None and restart:
            anchor_ms = self._estimate_start_ms(session, packet.sample_index)
        if restart:
            self._reset_audio_state(session)
            # A seek that declared no epoch is still the same undeclared stream,
            # so no number is invented for it: only a declared epoch names a new
            # stream, and adopting it is what lets a caption be attributed to
            # the stream the viewer is now watching.
            if packet.timeline_epoch is not None:
                session.timeline_epoch = packet.timeline_epoch
            if session.resampler is not None:
                session.resampler.reset()
        if anchor_ms is not None and (restart or not session.timeline.has_anchor):
            if restart:
                # The fresh stream starts at the media time this packet
                # announced, so its first sample carries that time and the
                # anchor is placed at the stream's own origin. Recording the
                # page's absolute sample counter here would make every caption
                # in the new stream late by the jump the viewer made.
                session.restart_origin = packet.sample_index
                session.timeline.rebase(
                    TimelineAnchor(
                        sample_index=0,
                        audio_start_ms=int(anchor_ms),
                        observed_at=_now(),
                        timeline_epoch=session.timeline_epoch,
                        playback_rate=packet.playback_rate,
                    )
                )
            else:
                session.timeline.add_anchor(
                    TimelineAnchor(
                        sample_index=session.stream_position,
                        audio_start_ms=int(anchor_ms),
                        observed_at=_now(),
                        timeline_epoch=session.timeline_epoch,
                        playback_rate=packet.playback_rate,
                    )
                )

        if session.resampler is None or session.resampler.in_rate != packet.sample_rate:
            session.resampler = Resampler(packet.sample_rate, session.sample_rate)
        assert session.resampler is not None

        span = max(int(samples.size), packet.sample_index + int(samples.size) - expected)
        hole = span - int(samples.size)
        if hole > 0:
            # A small hole is still stream time: the viewer's timeline does not
            # shrink because some audio was dropped, so the missing samples are
            # held as silence. Padding keeps every later sample on the video
            # timeline instead of pulling it forward.
            samples = np.concatenate((np.zeros(hole, dtype=samples.dtype), samples))

        resampled = session.resampler.process(samples)
        session.accepted_samples += int(resampled.size)
        self._push_audio(session, resampled)
        session.next_sample += int(resampled.size)
        # Advance by the span the packet actually covers, not only by the audio
        # it carried: after a 16-sample hole the later packets of the same batch
        # are each 16 samples further ahead, and counting only their own length
        # would charge that same hole once per packet instead of once.
        session.raw_since_restart += span

    @staticmethod
    def _estimate_start_ms(session: BridgeSession, sample_index: int) -> int:
        value = session.timeline.media_ms(sample_index)
        if value != value:  # nan
            return 0
        return max(0, int(round(value)))

    def _push_audio(self, session: BridgeSession, resampled: np.ndarray, *, real_end: int | None = None) -> None:
        """Feed newly arrived 16 kHz audio to the segmenter, one fixed frame at a time.

        ``frame_cursor`` tracks how far the segmenter has been fed and is always
        ``session.next_sample`` minus whatever is still carried. The remaining
        partial frame is carried, never zero-padded: padding invents audio and
        would shift every later sample position.
        """
        if resampled.size == 0:
            return
        base_sample = session.next_sample
        if base_sample + resampled.size > session.frame_cursor:
            # Only audio that has not been seen yet can be pushed.
            skip = max(0, session.frame_cursor - base_sample)
            resampled = resampled[skip:]
            base_sample += skip
        if resampled.size == 0:
            return
        segmenter = self._segmenter(session)
        session.carry = (
            np.concatenate((session.carry, resampled))
            if session.carry.size
            else resampled
        )
        frame_samples = self._frame_samples
        complete = session.carry.size // frame_samples
        for index in range(complete):
            frame = session.carry[index * frame_samples : (index + 1) * frame_samples]
            for span in segmenter.push_positioned(frame):
                if real_end is not None:
                    end = min(span.end_sample, real_end)
                    if end <= span.start_sample:
                        continue
                    span = SpeechSpan(span.samples[:end - span.start_sample], span.start_sample, end,
                                      overlap=span.overlap, forced_cut=span.forced_cut)
                self._enqueue(session, span, provisional=False)
        consumed = complete * frame_samples
        session.carry = session.carry[consumed:].copy()
        session.frame_cursor = base_sample + resampled.size

    def _segmenter(self, session: BridgeSession) -> Any:
        if session._segmenter is None:
            session._segmenter = self._segmenter_factory(
                sample_rate=RECOGNIZER_SAMPLE_RATE,
                frame_samples=self._frame_samples,
                silence_seconds=0.5,
                max_seconds=7.0,
                threshold=0.004,
            )
        return session._segmenter

    def _reset_audio_state(self, session: BridgeSession) -> None:
        """Drop audio state that belongs to the stream that just ended.

        Any clip still queued also belonged to that stream; recognizing it now
        would publish captions in coordinates the new stream no longer uses.
        """
        if session._segmenter is not None:
            session._segmenter.reset()
        session.carry = np.zeros(0, dtype=np.float32)
        session.frame_cursor = 0
        session.next_sample = 0
        session.raw_since_restart = 0
        with session.job_lock:
            session.jobs = []
        session.stream_complete = False
        session.language = session.request.source_language
        session.language_evidence.clear()
        session.language_confirmed = False
        session.last_language_clip = None

    def _snapshot(self, session: BridgeSession) -> SpeechSpan | None:
        segmenter = session._segmenter
        if segmenter is None:
            return None
        clip = segmenter.snapshot()
        if clip is None:
            return None
        start = segmenter.utterance_start_sample
        return SpeechSpan(
            samples=clip,
            start_sample=start,
            end_sample=start + int(clip.size),
            overlap=False,
            forced_cut=False,
        )

    def _flush_segmenter(self, session: BridgeSession) -> SpeechSpan | None:
        segmenter = session._segmenter
        if segmenter is None:
            return None
        span = segmenter.flush_positioned()
        if span is None:
            return None
        end = min(span.end_sample, session.next_sample)
        if end <= span.start_sample:
            return None
        return SpeechSpan(span.samples[:end - span.start_sample], span.start_sample, end,
                          overlap=span.overlap, forced_cut=span.forced_cut)

    def _enqueue(self, session: BridgeSession, span: SpeechSpan, *, provisional: bool) -> None:
        with session.job_lock:
            if provisional:
                session.jobs = [job for job in session.jobs if not job.provisional]
            session.jobs.append(
                ClipJob(
                    span=span,
                    provisional=provisional,
                    language=session.language,
                    epoch=session.timeline_epoch,
                )
            )
        session.idle.clear()

    def _wake(self, session: BridgeSession) -> None:
        session.wake.set()

    def _stop_worker(self, session: BridgeSession) -> None:
        session.stopped = True
        session.wake.set()
        worker = session.worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=5.0)

    def _start_worker(self, session: BridgeSession) -> None:
        session.idle.set()
        session.worker = threading.Thread(
            target=self._work, args=(session,), name=f"bridge-{session.session_id[:8]}", daemon=True
        )
        session.worker.start()

    def _work(self, session: BridgeSession) -> None:
        while not session.stopped:
            session.wake.wait(timeout=0.5)
            session.wake.clear()
            while True:
                with session.job_lock:
                    job = session.jobs.pop(0) if session.jobs else None
                if job is None:
                    break
                if job.epoch != session.timeline_epoch:
                    continue  # the stream was replaced while this clip waited
                session.busy = True
                try:
                    self._run_job(session, job)
                except Exception as exc:  # never kill the worker over one clip
                    session.warning = f"识别失败：{type(exc).__name__}: {exc}"
                finally:
                    session.busy = False
            if session.stream_complete and not session.jobs:
                session.idle.set()
            if session.stopped:
                break
        session.idle.set()

    def _run_job(self, session: BridgeSession, job: ClipJob) -> None:
        explicit = session.request.source_language not in ("auto", "")
        language = session.request.source_language if explicit else (
            session.language if session.language_confirmed else None
        )
        result = self.engine.transcribe_segments(
            job.span.samples,
            language=language,
            offset_samples=job.span.start_sample,
            word_timestamps=not job.provisional,
            beam_size=3,
        )
        if job.epoch != session.timeline_epoch:
            return
        if not result.segments:
            return
        if result.warning:
            session.warning = result.warning
        if explicit:
            session.language = session.request.source_language
        elif (not job.provisional and result.language and
              result.language_probability is not None and
              np.isfinite(result.language_probability) and result.language_probability >= 0.8 and
              job.span.duration_samples >= 2 * RECOGNIZER_SAMPLE_RATE and
              session.last_language_clip != job.span.start_sample and not session.language_confirmed):
            # Two independent final utterances avoid locking a whole video to
            # a low-confidence first fragment. These are project thresholds.
            session.last_language_clip = job.span.start_sample
            session.language_evidence = {result.language: session.language_evidence.get(result.language, 0) + 1}
            if session.language_evidence[result.language] >= 2:
                session.language_confirmed = True
                session.language = result.language
        elif session.language_confirmed:
            session.language = result.language
        self._publish(
            session, result, provisional=job.provisional, epoch=job.epoch,
            clip_start_sample=job.span.start_sample,
        )

    def _publish(
        self,
        session: BridgeSession,
        result: TranscriptResult,
        *,
        provisional: bool,
        epoch: int | None = None,
        clip_start_sample: int | None = None,
    ) -> None:
        language = result.language or session.language
        for index, segment in enumerate(result.segments):
            start_sample = (
                segment.start_sample if segment.start_sample is not None else session.next_sample
            )
            end_sample = (
                segment.end_sample if segment.end_sample is not None else start_sample + 1
            )
            start_ms = self._media_ms_or_fallback(session, start_sample, segment.start_ms)
            end_ms = self._media_ms_or_fallback(session, end_sample, segment.end_ms)
            if end_ms < start_ms:
                end_ms = start_ms
            with session.store_lock:
                session.revision += 1
                revision = session.revision
            # Model refinements may move a sentence's start. Its identity is
            # the input clip/slot, while its timing follows the model revision.
            origin = start_sample if clip_start_sample is None else clip_start_sample
            stream_epoch = session.timeline_epoch if epoch is None else epoch
            identifier = f"{session.session_id[:8]}-{stream_epoch}-{origin}-{index}"
            view = SegmentView(
                segment_id=identifier,
                revision=revision,
                source_language=language,
                original=segment.text,
                german="",
                start_ms=start_ms,
                end_ms=end_ms,
                start_sample=start_sample,
                end_sample=end_sample,
                words=tuple({**word.to_dict(),
                    "startMs": self._media_ms_or_fallback(session, word.start_sample, word.start_ms)
                        if word.start_sample is not None else start_ms + word.start_ms - segment.start_ms,
                    "endMs": self._media_ms_or_fallback(session, word.end_sample, word.end_ms)
                        if word.end_sample is not None else start_ms + word.end_ms - segment.start_ms,
                } for word in segment.words),
                provisional=provisional,
                # The stream a caption belongs to is the one its audio came
                # from, not whichever stream is current by the time the worker
                # got around to recognizing it.
                timeline_epoch=session.timeline_epoch if epoch is None else epoch,
            )
            stored = StoredSegment(view=view, source=segment)
            with session.store_lock:
                session.revisions.append(stored)
                if len(session.revisions) > 4096:
                    del session.revisions[:1024]
            session.recognized_segments += 1
            self.status.recognized_segments += 1
            if provisional:
                continue
            self._translate(session, stored, language)

    def _translate(self, session: BridgeSession, stored: StoredSegment, language: str) -> None:
        if stored.translation_attempted:
            return
        stored.translation_attempted = True
        translation = self.engine.translate(stored.source.text, language)
        session.translation_backend = translation.backend
        if translation.failed:
            session.translation_failures += 1
            self.status.translation_failures += 1
        with session.store_lock:
            session.revision += 1
            revision = session.revision
        view = stored.view
        stored.view = SegmentView(
            segment_id=view.segment_id,
            revision=revision,
            source_language=view.source_language,
            original=view.original,
            german=translation.text or view.original,
            start_ms=view.start_ms,
            end_ms=view.end_ms,
            start_sample=view.start_sample,
            end_sample=view.end_sample,
            words=view.words,
            translation_language=translation.language,
            translation_backend=translation.backend,
            translation_failed=translation.failed,
            provisional=False,
            timeline_epoch=view.timeline_epoch,
        )

    def _media_ms_or_fallback(
        self, session: BridgeSession, sample_index: int, fallback_ms: int
    ) -> int:
        value = session.timeline.media_ms(sample_index)
        if value != value:  # nan: no anchor published yet
            return int(fallback_ms)
        return max(0, int(round(value)))

    # ------------------------------------------------------------------ results

    def transcripts(self, session: BridgeSession, query: TranscriptQuery) -> TranscriptResponse:
        session.touch()
        if query.wait_seconds:
            deadline = _now() + query.wait_seconds
            while _now() < deadline:
                segments, revision = session.segments_since(query.since_revision)
                if revision > query.since_revision:
                    break
                if session.stream_complete and not session.busy and not session.jobs:
                    break
                session.wake.set()
                time.sleep(0.05)
        segments, revision = session.segments_since(query.since_revision)
        return TranscriptResponse(
            segments=segments,
            revision=revision,
            status=session.status(),
            language=session.language,
            progress_ms=session.progress_ms(),
            translation_backend=session.translation_backend,
        )


def _default_segmenter_factory(**kwargs: Any) -> PositionedSpeechSegmenter:
    return PositionedSpeechSegmenter(**kwargs)
