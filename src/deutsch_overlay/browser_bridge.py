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
import re
from collections import OrderedDict, deque
from dataclasses import dataclass, field, replace
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
    MAX_SESSION_SECONDS,
    SUPPORTED_TRANSLATION_TARGETS,
)
from .pcm import (
    AudioTimeline,
    RECOGNIZER_SAMPLE_RATE,
    Resampler,
    TimelineAnchor,
    decode_packet,
)
from .transcript import TranscriptResult, TranscriptSegment, TranscriptTranslation

GAP_TOLERANCE_SECONDS = 0.5
MAX_SNAPSHOT_REVISIONS = 64
DEFAULT_SESSION_IDLE_TTL_SECONDS = 2 * 60 * 60


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
    generation: int = 0
    queued_at: float = field(default_factory=_now)
    beam_size: int = 3


@dataclass(slots=True)
class TranslationJob:
    stored: StoredSegment
    language: str
    generation: int
    queued_at: float = field(default_factory=_now)


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
    generation: int = 0
    state_lock: Any = field(default_factory=threading.RLock)
    translation_jobs: list[TranslationJob] = field(default_factory=list)
    translation_worker: threading.Thread | None = None
    translation_wake: threading.Event = field(default_factory=threading.Event)
    translation_busy: bool = False
    translation_cache: Any = field(default_factory=OrderedDict)
    retained_clips: Any = field(default_factory=OrderedDict)
    segment_clips: dict[str, int] = field(default_factory=dict)
    metrics: dict[str, int | float] = field(default_factory=lambda: {
        "droppedClips": 0, "droppedTranslations": 0, "translationCacheHits": 0,
        "recognitionMs": 0, "translationMs": 0, "recognitionQueueMs": 0, "translationQueueMs": 0,
    })
    latency_samples: dict[str, Any] = field(default_factory=dict)
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
            if self.translation_busy or self.translation_jobs:
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
            return tuple(view for view in grouped.values() if not view.removed)

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
        model_profiles: dict[str, Any] | None = None,
        model_profile: str = "small",
        max_clip_jobs: int = 8,
        max_translation_jobs: int = 32,
        session_idle_ttl_seconds: float = DEFAULT_SESSION_IDLE_TTL_SECONDS,
        session_max_seconds: float = MAX_SESSION_SECONDS,
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
        self._config_lock = threading.RLock()
        self._retired_sessions: list[BridgeSession] = []
        self.model_profiles = dict(model_profiles or {model_profile: getattr(engine, "asr_model_path", None)})
        self.model_profile = model_profile
        self.translation_context = False
        self.max_clip_jobs = max(1, max_clip_jobs)
        self.max_translation_jobs = max(1, max_translation_jobs)
        if not isinstance(session_idle_ttl_seconds, (int, float)) or session_idle_ttl_seconds <= 0:
            raise ValueError("session_idle_ttl_seconds must be positive")
        if not isinstance(session_max_seconds, (int, float)) or session_max_seconds <= 0:
            raise ValueError("session_max_seconds must be positive")
        self.session_idle_ttl_seconds = float(session_idle_ttl_seconds)
        self.session_max_seconds = float(session_max_seconds)

    def settings(self) -> dict[str, Any]:
        with self._config_lock:
            return {"ok": True, "settings": {
                "modelProfile": self.model_profile,
                "chineseScript": getattr(self.engine, "chinese_script", "raw"),
                "hotwords": {lang: getattr(self.engine, "hotwords", {}).get(lang, "") for lang in ("de", "en", "zh")},
                "translationContext": self.translation_context,
            }, "profiles": [{"id": key, "label": {"small": "Small（轻量）", "turbo": "Turbo（高精度）", "configured": "启动时指定的模型"}.get(key, key)} for key in self.model_profiles],
                "device": ("cuda" if getattr(self.engine, "_using_gpu", False) else "cpu") if self.prepared else "unloaded",
                "modelReady": self.prepared, "warning": self.prepare_error or getattr(self.engine, "warning", "") or ""}

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Validate everything before changing anything. Browser input can select
        # a registered profile, never a filesystem path or a model download.
        with self._config_lock:
            if not isinstance(payload, dict) or set(payload) - {"modelProfile", "chineseScript", "hotwords", "translationContext"}:
                raise ProtocolError(ErrorCode.BAD_REQUEST, "unknown recognition setting")
            current = self.settings()["settings"]
            values = {**current, **payload}
            profile, script, terms = values["modelProfile"], values["chineseScript"], values["hotwords"]
            if not isinstance(profile, str) or profile not in self.model_profiles:
                raise ProtocolError(ErrorCode.BAD_REQUEST, "modelProfile is not registered")
            if script not in ("raw", "simplified", "traditional") or not isinstance(values["translationContext"], bool):
                raise ProtocolError(ErrorCode.BAD_REQUEST, "invalid script or context setting")
            if not isinstance(terms, dict) or any(
                lang not in ("de", "en", "zh") or not isinstance(text, str) or len(text) > 1000
                or any(ord(char) < 32 or ord(char) == 127 for char in text)
                for lang, text in terms.items()
            ):
                raise ProtocolError(ErrorCode.BAD_REQUEST, "hotwords must contain de/en/zh strings, at most 1000 characters, without controls")
            with self._lock:
                sessions = list(self.sessions.values()) + self._retired_sessions
                if any((s.recognize and not s.stopped) or s.busy or s.translation_busy for s in sessions):
                    raise ProtocolError(ErrorCode.CONFLICT, "请先停止所有识别会话，再修改设置")
                self._retired_sessions = [s for s in self._retired_sessions if s.busy or s.translation_busy]
            if profile != self.model_profile:
                from .engines.local import LocalEngine
                if not isinstance(self.engine, LocalEngine):
                    raise ProtocolError(ErrorCode.BAD_REQUEST, "engine does not support model switching")
                try:
                    engine = LocalEngine(model_store=self.engine.store, asr_factory=self.engine.asr_factory,
                        translators=self.engine.translators, prefer_gpu=self.engine.prefer_gpu,
                        asr_model_path=self.model_profiles[profile], chinese_script=script, hotwords=terms)
                except (ValueError, OSError):
                    raise ProtocolError(ErrorCode.BAD_REQUEST, "registered model is unavailable") from None
                self.engine = engine
                self.model_profile = profile
                self.prepared = False
                self.prepare_error = ""
            else:
                self.engine.chinese_script = script
                self.engine.hotwords = dict(terms)
                self.engine._chinese_converter = None
            self.translation_context = values["translationContext"]
            return self.settings()

    # ------------------------------------------------------------------ health

    @property
    def engine_name(self) -> str:
        return getattr(self.engine, "name", "") or type(self.engine).__name__

    def health(self) -> dict[str, Any]:
        self._reap_expired_sessions()
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
            try:
                self.engine.prepare()
                self.prepared = True
            except Exception as exc:  # surfaced through /v1/health instead of crashing
                self.prepared = False
                self.prepare_error = f"模型准备失败（{type(exc).__name__}）；请检查本机模型"

    # ----------------------------------------------------------------- sessions

    def create_session(self, request: SessionRequest) -> SessionResponse:
        with self._config_lock:
            return self._create_session(request)

    def _create_session(self, request: SessionRequest) -> SessionResponse:
        self._reap_expired_sessions()
        if request.translation_target not in SUPPORTED_TRANSLATION_TARGETS:
            raise ProtocolError(ErrorCode.LANGUAGE_UNSUPPORTED,
                f"translation target is not supported: {request.translation_target}")
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
        self._reap_expired_sessions()
        with self._lock:
            session = self.sessions.get(session_id)
        if session is None:
            raise ProtocolError(ErrorCode.NOT_FOUND, f"unknown session: {session_id}")
        return session

    def _reap_expired_sessions(self) -> int:
        """Stop only idle sessions whose client stopped touching the bridge.

        The monotonic timestamp is deliberately kept on the session, so wall
        clock changes cannot prolong a leaked tab session or expire an active
        one. Busy inference is allowed to finish and is reaped on its next
        idle check.
        """
        now = _now()
        expired: list[BridgeSession] = []
        with self._lock:
            for session_id, session in list(self.sessions.items()):
                if session.stopped or session.busy or session.translation_busy:
                    continue
                idle_for = now - session.last_touch
                age = now - session.created_at
                if idle_for < self.session_idle_ttl_seconds and age < self.session_max_seconds:
                    continue
                self.sessions.pop(session_id, None)
                expired.append(session)
            if expired:
                self.status.sessions = dict(self.sessions)
        for session in expired:
            self._stop_worker(session)
        return len(expired)

    def close_session(self, session_id: str) -> int:
        session = self.get(session_id)
        self._stop_worker(session)
        with self._lock:
            self.sessions.pop(session_id, None)
            self._retired_sessions = [s for s in self._retired_sessions if s.busy or s.translation_busy]
            self._retired_sessions.append(session)
            self.status.sessions = dict(self.sessions)
        return len(session.revisions)

    def close_all(self) -> None:
        with self._config_lock:
            with self._lock:
                sessions = list(self.sessions.values()) + self._retired_sessions
                self.sessions.clear()
                self.status.sessions = {}
            for session in sessions:
                self._stop_worker(session)
            with self._lock:
                self._retired_sessions = [s for s in sessions if s.busy or s.translation_busy]

    # -------------------------------------------------------------- audio input

    def feed(self, session: BridgeSession, request: AudioRequest) -> AudioResponse:
        with session.state_lock:
            if session.stopped:
                raise ProtocolError(ErrorCode.CONFLICT, "session is closed")
            return self._feed(session, request)

    def _feed(self, session: BridgeSession, request: AudioRequest) -> AudioResponse:
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
            session.translation_jobs = []
            session.generation += 1
        session.translation_cache.clear()
        session.retained_clips.clear()
        session.segment_clips.clear()
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
                    generation=session.generation,
                )
            )
            if len(session.jobs) > self.max_clip_jobs:
                session.jobs.pop(0)
                session.metrics["droppedClips"] += 1
                session.warning = "识别处理较慢，已跳过积压的旧音频；请降低播放速度"
            session.idle.clear()

    def _wake(self, session: BridgeSession) -> None:
        session.wake.set()

    def _stop_worker(self, session: BridgeSession) -> None:
        with session.state_lock:
            session.stopped = True
            session.generation += 1
            with session.job_lock:
                session.jobs.clear()
                session.translation_jobs.clear()
            session.retained_clips.clear()
            session.segment_clips.clear()
            session.translation_cache.clear()
        session.wake.set()
        session.translation_wake.set()
        for worker in (session.worker, session.translation_worker):
            if worker is not None and worker.is_alive():
                worker.join(timeout=1.0)

    def _start_worker(self, session: BridgeSession) -> None:
        with session.job_lock:
            self._update_idle_locked(session)
        session.worker = threading.Thread(
            target=self._work, args=(session,), name=f"bridge-{session.session_id[:8]}", daemon=True
        )
        session.worker.start()
        session.translation_worker = threading.Thread(
            target=self._translation_work, args=(session,), name=f"translate-{session.session_id[:8]}", daemon=True
        )
        session.translation_worker.start()

    @staticmethod
    def _update_idle_locked(session: BridgeSession) -> None:
        if not (session.busy or session.jobs or session.translation_busy or session.translation_jobs):
            session.idle.set()
        else:
            session.idle.clear()

    @staticmethod
    def _job_current(session: BridgeSession, job: ClipJob | TranslationJob) -> bool:
        epoch = job.epoch if isinstance(job, ClipJob) else job.stored.view.timeline_epoch
        return not session.stopped and job.generation == session.generation and epoch == session.timeline_epoch

    @staticmethod
    def _record_latency(session: BridgeSession, key: str, milliseconds: float) -> None:
        samples = session.latency_samples.setdefault(key, deque(maxlen=128))
        samples.append(max(0, milliseconds))
        session.metrics[key + "Ms"] = round(milliseconds)
        session.metrics[key + "P95Ms"] = round(float(np.percentile(list(samples), 95)))

    def _work(self, session: BridgeSession) -> None:
        while not session.stopped:
            session.wake.wait(timeout=0.5)
            session.wake.clear()
            while True:
                with session.job_lock:
                    job = session.jobs.pop(0) if session.jobs else None
                    session.busy = job is not None
                    self._update_idle_locked(session)
                if job is None:
                    break
                try:
                    if self._job_current(session, job):
                        self._record_latency(session, "recognitionQueue", (_now() - job.queued_at) * 1000)
                        started = _now()
                        self._run_job(session, job)
                        self._record_latency(session, "recognition", (_now() - started) * 1000)
                except Exception as exc:  # never kill the worker over one clip
                    if self._job_current(session, job):
                        session.warning = f"识别失败（{type(exc).__name__}）"
                finally:
                    with session.job_lock:
                        session.busy = False
                        self._update_idle_locked(session)
            if session.stopped:
                break
        with session.job_lock:
            self._update_idle_locked(session)

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
            beam_size=job.beam_size,
        )
        with session.state_lock:
            self._complete_recognition(session, job, result)

    def _complete_recognition(self, session: BridgeSession, job: ClipJob, result: TranscriptResult) -> None:
        explicit = session.request.source_language not in ("auto", "")
        if not self._job_current(session, job):
            return
        if job.beam_size > 3:
            self._remove_surplus_slots(session, job, len(result.segments))
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
        if not job.provisional:
            session.retained_clips[job.span.start_sample] = job
            session.retained_clips.move_to_end(job.span.start_sample)
            while len(session.retained_clips) > 8 or sum(j.span.samples.size for j in session.retained_clips.values()) > 60 * RECOGNIZER_SAMPLE_RATE:
                origin, _ = session.retained_clips.popitem(last=False)
                session.segment_clips = {key: value for key, value in session.segment_clips.items() if value != origin}

    @staticmethod
    def _remove_surplus_slots(session: BridgeSession, job: ClipJob, count: int) -> None:
        """A retry may split the same clip into fewer sentences, including zero."""
        prefix = f"{session.session_id[:8]}-{job.epoch}-{job.span.start_sample}-"
        with session.store_lock:
            latest = {stored.view.segment_id: stored for stored in session.revisions}
            for identifier, stored in latest.items():
                if not identifier.startswith(prefix) or stored.view.removed:
                    continue
                slot = identifier[len(prefix):]
                if not slot.isdigit() or int(slot) < count:
                    continue
                session.revision += 1
                stored.view = replace(stored.view, revision=session.revision, removed=True,
                    words=(), german="", translation_group_ids=())
                session.segment_clips.pop(identifier, None)

    def retry_segment(self, session: BridgeSession, payload: dict[str, Any]) -> dict[str, Any]:
        with session.state_lock:
            if not isinstance(payload.get("segmentId"), str) or type(payload.get("timelineEpoch")) is not int:
                raise ProtocolError(ErrorCode.BAD_REQUEST, "segmentId and timelineEpoch are required")
            if not session.recognize or session.stopped or payload["timelineEpoch"] != session.timeline_epoch:
                raise ProtocolError(ErrorCode.CONFLICT, "该片段已过期，请重新播放")
            origin = session.segment_clips.get(payload["segmentId"])
            job = session.retained_clips.get(origin)
            if job is None or not self._job_current(session, job):
                raise ProtocolError(ErrorCode.CONFLICT, "该片段音频已过期，请重新播放")
            with session.job_lock:
                if len(session.jobs) >= self.max_clip_jobs:
                    raise ProtocolError(ErrorCode.ENGINE_BUSY, "识别队列已满，请稍后重试")
                session.jobs.append(replace(job, queued_at=_now(), beam_size=5))
                session.idle.clear()
            session.wake.set()
            return {"ok": True, "queued": True, "segmentId": payload["segmentId"], "timelineEpoch": session.timeline_epoch}

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
            reasons = []
            logprob = segment.avg_logprob if segment.avg_logprob is not None and np.isfinite(segment.avg_logprob) else None
            no_speech = segment.no_speech_probability if segment.no_speech_probability is not None and np.isfinite(segment.no_speech_probability) else None
            if logprob is None or no_speech is None:
                reasons.append("scores_unavailable")
            if logprob is not None and logprob < -1.0:
                reasons.append("low_log_probability")
            if no_speech is not None and no_speech > 0.6:
                reasons.append("possible_non_speech")
            view = SegmentView(
                segment_id=identifier,
                revision=revision,
                source_language=language,
                original=segment.text,
                raw_original=segment.raw_text or segment.text,
                avg_logprob=logprob, no_speech_probability=no_speech,
                uncertain=bool(reasons), uncertainty_reasons=tuple(reasons),
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
            session.segment_clips[identifier] = origin
            self.status.recognized_segments += 1
            if provisional:
                continue
            with session.job_lock:
                session.translation_jobs.append(TranslationJob(stored, language, session.generation))
                if len(session.translation_jobs) > self.max_translation_jobs:
                    dropped = session.translation_jobs.pop(0)
                    session.metrics["droppedTranslations"] += 1
                    session.warning = "翻译处理较慢，已跳过积压译文并保留原文"
                    self._apply_translation(session, [dropped], TranscriptTranslation(language=dropped.language, text="", backend="skipped", failed=True))
                session.idle.clear()
            session.translation_wake.set()

    def _translation_work(self, session: BridgeSession) -> None:
        while not session.stopped:
            session.translation_wake.wait(timeout=0.1)
            session.translation_wake.clear()
            while not session.stopped:
                with session.state_lock:
                    with session.job_lock:
                        jobs = [session.translation_jobs.pop(0)] if session.translation_jobs else []
                        session.translation_busy = bool(jobs)
                        self._update_idle_locked(session)
                if not jobs:
                    break
                try:
                    first = jobs[0]
                    if not self._job_current(session, first):
                        continue
                    # Give an adjacent fragment a short bounded chance to arrive.
                    # Complete sentences, German identity and stream end never wait.
                    if self.translation_context and first.language in ("en", "zh"):
                        if not self._sentence_complete(first.stored.source.text) and not session.stream_complete:
                            session.translation_wake.wait(timeout=0.25)
                            session.translation_wake.clear()
                        with session.job_lock:
                            while session.translation_jobs and len(jobs) < 3:
                                candidate = session.translation_jobs[0]
                                prev = jobs[-1]
                                gap = candidate.stored.view.start_ms - prev.stored.view.end_ms
                                if (self._sentence_complete(prev.stored.source.text) or candidate.language != first.language
                                        or candidate.generation != first.generation or not 0 <= gap <= 800
                                        or sum(len(j.stored.source.text) for j in jobs) + len(candidate.stored.source.text) > 240):
                                    break
                                jobs.append(session.translation_jobs.pop(0))
                    self._record_latency(session, "translationQueue", (_now() - first.queued_at) * 1000)
                    source = ("" if first.language == "zh" else " ").join(j.stored.source.text.strip() for j in jobs)
                    key = (first.language, source)
                    started = _now()
                    with session.state_lock:
                        translation = session.translation_cache.get(key)
                    if translation is not None:
                        session.metrics["translationCacheHits"] += 1
                    else:
                        try:
                            translation = self.engine.translate(source, first.language)
                        except Exception:
                            translation = TranscriptTranslation(language=first.language, text="", backend="failed", failed=True)
                    self._record_latency(session, "translation", (_now() - started) * 1000)
                    with session.state_lock:
                        if self._job_current(session, first):
                            if not translation.failed:
                                session.translation_cache[key] = translation
                                session.translation_cache.move_to_end(key)
                                while len(session.translation_cache) > 128:
                                    session.translation_cache.popitem(last=False)
                            self._apply_translation(session, jobs, translation)
                finally:
                    with session.job_lock:
                        session.translation_busy = False
                        self._update_idle_locked(session)

    @staticmethod
    def _sentence_complete(text: str) -> bool:
        return bool(re.search(r'[.!?。！？][\"\'”’）)]*$', text.strip()))

    def _apply_translation(self, session: BridgeSession, jobs: list[TranslationJob], translation: TranscriptTranslation) -> None:
        if not jobs or not self._job_current(session, jobs[0]):
            return
        session.translation_backend = translation.backend
        if translation.failed:
            session.translation_failures += 1
            self.status.translation_failures += 1
        group = tuple(job.stored.view.segment_id for job in jobs) if len(jobs) > 1 else ()
        with session.store_lock:
            for job in jobs:
                stored = job.stored
                if stored.view.removed:
                    continue
                # A re-recognition may already have replaced the same ID.
                if any(item.view.segment_id == stored.view.segment_id and item is not stored
                       and item.view.revision > stored.view.revision for item in session.revisions):
                    continue
                session.revision += 1
                stored.translation_attempted = True
                stored.view = replace(stored.view, revision=session.revision,
                    german=translation.text if not translation.failed else stored.view.original,
                    translation_language=translation.language, translation_backend=translation.backend,
                    translation_failed=translation.failed, provisional=False, translation_group_ids=group)

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
                if session.stream_complete and session.idle.is_set():
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
            metrics={**session.metrics, "queuedClips": len(session.jobs), "queuedTranslations": len(session.translation_jobs)},
            warning=session.warning,
        )


def _default_segmenter_factory(**kwargs: Any) -> PositionedSpeechSegmenter:
    return PositionedSpeechSegmenter(**kwargs)
