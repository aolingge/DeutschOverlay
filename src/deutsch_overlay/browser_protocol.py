"""Wire protocol between the browser extension and the local bridge.

Version 1 of the loopback protocol. Everything here is pure data plus
validation, so both sides can be tested without sockets, threads, or a
running recognizer.

Design rules that the rest of the system depends on:

* Recognition and translation are separate steps. A segment is published
  with its original text first; a translation revision follows.
* Media time comes from the *published* browser timeline (``audioStartMs`` /
  ``timelineEpoch``), never from when recognition finished.
* The bridge refuses to recognize audio for a video that already has a
  usable caption track. The extension states what it found; the bridge
  enforces the decision.
* Every packet declares the sample index its first sample occupies in the
  session's submitted-audio stream, so a dropped packet shows up as a gap
  instead of silently shifting every later caption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
MAX_SESSION_SECONDS = 6 * 60 * 60
# 1 MiB of base64 expands to ~768 KiB, i.e. ~12 s of 16-bit 16 kHz mono.
MAX_PACKET_BASE64_CHARS = 1 << 20
MAX_PACKETS_PER_REQUEST = 16
MAX_LEAD_SECONDS = 30.0

SUPPORTED_LANGUAGES = ("de", "en", "zh")
SUPPORTED_ENCODINGS = ("pcm_s16le",)
SUPPORTED_SOURCE_KINDS = ("tab_capture", "local_file")


class ProtocolError(ValueError):
    """A request that a correct client must not have sent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ErrorCode:
    BAD_REQUEST = "bad_request"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN_ORIGIN = "forbidden_origin"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    TOO_LARGE = "too_large"
    ENGINE_BUSY = "engine_busy"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    CAPTIONS_PRESENT = "captions_present"
    LANGUAGE_UNSUPPORTED = "language_unsupported"
    TIMELINE_GAP = "timeline_gap"
    TIMELINE_REGRESSION = "timeline_regression"
    INTERNAL = "internal"


class Status:
    RECOGNIZING = "recognizing"
    TRANSLATING = "translating"
    READY = "ready"
    EMPTY = "empty"


class CaptionAvailability:
    """What the page reported about native captions.

    ``UNKNOWN`` is deliberately treated like "has captions": recognition
    starts only when the extension positively determined there is nothing to
    show, because recognizing a video that already has subtitles wastes the
    local model and duplicates the overlay.
    """

    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class SourceKind:
    TAB_CAPTURE = "tab_capture"
    LOCAL_FILE = "local_file"


def _require_mapping(data: Any, what: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ProtocolError(ErrorCode.BAD_REQUEST, f"{what} must be a JSON object")
    return data


def _require_str(data: dict[str, Any], key: str, *, max_length: int = 4096) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(ErrorCode.BAD_REQUEST, f"{key} must be a non-empty string")
    if len(value) > max_length:
        raise ProtocolError(ErrorCode.BAD_REQUEST, f"{key} is too long")
    return value


def _optional_str(data: dict[str, Any], key: str, *, max_length: int = 4096) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > max_length:
        raise ProtocolError(ErrorCode.BAD_REQUEST, f"{key} must be a string")
    return value


def _require_int(data: dict[str, Any], key: str, *, minimum: int | None = None) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(ErrorCode.BAD_REQUEST, f"{key} must be an integer")
    if minimum is not None and value < minimum:
        raise ProtocolError(ErrorCode.BAD_REQUEST, f"{key} must be >= {minimum}")
    return value


def _optional_float(data: dict[str, Any], key: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(ErrorCode.BAD_REQUEST, f"{key} must be a number")
    return float(value)


def _require_choice(data: dict[str, Any], key: str, allowed: tuple[str, ...]) -> str:
    value = data.get(key)
    if value not in allowed:
        raise ProtocolError(
            ErrorCode.BAD_REQUEST, f"{key} must be one of {', '.join(allowed)}"
        )
    return value


def _optional_language(data: dict[str, Any], key: str) -> str:
    value = data.get(key) or "auto"
    if value == "auto":
        return "auto"
    if value not in SUPPORTED_LANGUAGES:
        raise ProtocolError(
            ErrorCode.LANGUAGE_UNSUPPORTED,
            f"{key} must be auto or one of {', '.join(SUPPORTED_LANGUAGES)}",
        )
    return value


@dataclass(frozen=True, slots=True)
class HealthResponse:
    protocol_version: int
    service: str
    model_ready: bool
    engine: str
    languages: tuple[str, ...] = SUPPORTED_LANGUAGES
    warning: str = ""
    sessions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "protocolVersion": self.protocol_version,
            "service": self.service,
            "modelReady": self.model_ready,
            "engine": self.engine,
            "languages": list(self.languages),
            "warning": self.warning,
            "sessions": self.sessions,
        }


@dataclass(frozen=True, slots=True)
class SessionRequest:
    """Everything the extension must state before recognition is allowed."""

    platform: str
    video_key: str
    caption_availability: str
    source_kind: str = SourceKind.TAB_CAPTURE
    source_language: str = "auto"
    # ``None`` means the page did not declare an epoch at all, which is not the
    # same claim as declaring epoch 0: the first keeps the stream's identity
    # unknown, the second starts a numbered one.
    timeline_epoch: int | None = None
    audio_start_ms: int | None = None
    playback_rate: float | None = None
    title: str = ""
    url: str = ""
    duration_ms: int | None = None
    has_audio_track: bool | None = None
    sampled_packets: int = 0
    observed_text_chars: int = 0
    client: str = ""
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocolVersion": self.protocol_version,
            "platform": self.platform,
            "videoKey": self.video_key,
            "title": self.title,
            "url": self.url,
            "captionAvailability": self.caption_availability,
            "sourceKind": self.source_kind,
            "sourceLanguage": self.source_language,
            "timelineEpoch": self.timeline_epoch,
            "audioStartMs": self.audio_start_ms,
            "playbackRate": self.playback_rate,
            "durationMs": self.duration_ms,
            "hasAudioTrack": self.has_audio_track,
            "sampledPackets": self.sampled_packets,
            "observedTextChars": self.observed_text_chars,
            "client": self.client,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "SessionRequest":
        data = _require_mapping(data, "request")
        version = data.get("protocolVersion", PROTOCOL_VERSION)
        if isinstance(version, bool) or not isinstance(version, int):
            raise ProtocolError(
                ErrorCode.BAD_REQUEST, "protocolVersion must be an integer"
            )
        if version != PROTOCOL_VERSION:
            raise ProtocolError(
                ErrorCode.UNSUPPORTED_VERSION,
                f"bridge speaks protocol {PROTOCOL_VERSION}, client speaks {version}",
            )
        availability = _require_choice(
            data,
            "captionAvailability",
            (CaptionAvailability.PRESENT, CaptionAvailability.ABSENT, CaptionAvailability.UNKNOWN),
        )
        source_kind = data.get("sourceKind", SourceKind.TAB_CAPTURE)
        if source_kind not in SUPPORTED_SOURCE_KINDS:
            raise ProtocolError(
                ErrorCode.BAD_REQUEST,
                f"sourceKind must be one of {', '.join(SUPPORTED_SOURCE_KINDS)}",
            )
        start_ms = data.get("audioStartMs")
        if start_ms is not None:
            if isinstance(start_ms, bool) or not isinstance(start_ms, int) or start_ms < 0:
                raise ProtocolError(
                    ErrorCode.BAD_REQUEST, "audioStartMs must be a non-negative integer"
                )
        else:
            start_ms = None
        if source_kind == SourceKind.TAB_CAPTURE and start_ms is None:
            raise ProtocolError(
                ErrorCode.BAD_REQUEST,
                "audioStartMs is required for tab capture so captions can be timed to the video",
            )
        has_audio = data.get("hasAudioTrack")
        if has_audio is not None and not isinstance(has_audio, bool):
            raise ProtocolError(ErrorCode.BAD_REQUEST, "hasAudioTrack must be a boolean")
        rate = _optional_float(data, "playbackRate")
        if rate is not None and rate <= 0:
            raise ProtocolError(ErrorCode.BAD_REQUEST, "playbackRate must be positive")
        return cls(
            platform=_require_str(data, "platform", max_length=32),
            video_key=_require_str(data, "videoKey", max_length=64),
            caption_availability=availability,
            source_kind=source_kind,
            source_language=_optional_language(data, "sourceLanguage"),
            timeline_epoch=_require_int(data, "timelineEpoch", minimum=0)
            if "timelineEpoch" in data
            else None,
            audio_start_ms=start_ms,
            playback_rate=rate,
            title=_optional_str(data, "title", max_length=512),
            url=_optional_str(data, "url", max_length=2048),
            duration_ms=_require_int(data, "durationMs", minimum=0)
            if isinstance(data.get("durationMs"), int) and not isinstance(data.get("durationMs"), bool)
            else None,
            has_audio_track=has_audio,
            sampled_packets=_require_int(data, "sampledPackets", minimum=0)
            if isinstance(data.get("sampledPackets"), int)
            and not isinstance(data.get("sampledPackets"), bool)
            else 0,
            observed_text_chars=_require_int(data, "observedTextChars", minimum=0)
            if isinstance(data.get("observedTextChars"), int)
            and not isinstance(data.get("observedTextChars"), bool)
            else 0,
            client=_optional_str(data, "client", max_length=128),
            protocol_version=version,
        )


@dataclass(frozen=True, slots=True)
class SessionResponse:
    session_id: str
    protocol_version: int
    engine: str
    source_language: str
    recognize: bool
    reason: str
    message: str = ""
    accepted_from_sample: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "sessionId": self.session_id,
            "protocolVersion": self.protocol_version,
            "engine": self.engine,
            "sourceLanguage": self.source_language,
            "recognize": self.recognize,
            "reason": self.reason,
            "message": self.message,
            "acceptedFromSample": self.accepted_from_sample,
        }


@dataclass(frozen=True, slots=True)
class AudioPacket:
    """One contiguous block of PCM, placed on the session's sample stream."""

    sequence: int
    sample_index: int
    pcm_base64: str
    duration_ms: int | None = None
    language: str = "auto"
    encoding: str = "pcm_s16le"
    sample_rate: int = 16000
    channels: int = 1
    audio_start_ms: int | None = None
    timeline_epoch: int | None = None
    playback_rate: float | None = None
    final: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "sampleIndex": self.sample_index,
            "pcm": self.pcm_base64,
            "encoding": self.encoding,
            "sampleRate": self.sample_rate,
            "channels": self.channels,
            "language": self.language,
            "audioStartMs": self.audio_start_ms,
            "timelineEpoch": self.timeline_epoch,
            "playbackRate": self.playback_rate,
            "final": self.final,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "AudioPacket":
        data = _require_mapping(data, "packet")
        pcm = data.get("pcm")
        if not isinstance(pcm, str) or not pcm:
            raise ProtocolError(ErrorCode.BAD_REQUEST, "pcm must be a base64 string")
        if len(pcm) > MAX_PACKET_BASE64_CHARS:
            raise ProtocolError(
                ErrorCode.TOO_LARGE,
                f"pcm exceeds {MAX_PACKET_BASE64_CHARS} base64 characters",
            )
        encoding = data.get("encoding", "pcm_s16le")
        if encoding not in SUPPORTED_ENCODINGS:
            raise ProtocolError(
                ErrorCode.BAD_REQUEST,
                f"encoding must be one of {', '.join(SUPPORTED_ENCODINGS)}",
            )
        sample_rate = _require_int(data, "sampleRate", minimum=1)
        channels = data.get("channels", 1)
        if isinstance(channels, bool) or not isinstance(channels, int) or channels not in (1, 2):
            raise ProtocolError(ErrorCode.BAD_REQUEST, "channels must be 1 or 2")
        epoch = data.get("timelineEpoch")
        if epoch is not None:
            if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
                raise ProtocolError(
                    ErrorCode.BAD_REQUEST, "timelineEpoch must be a non-negative integer"
                )
        start_ms = data.get("audioStartMs")
        if start_ms is not None:
            if isinstance(start_ms, bool) or not isinstance(start_ms, int) or start_ms < 0:
                raise ProtocolError(
                    ErrorCode.BAD_REQUEST, "audioStartMs must be a non-negative integer"
                )
        rate = _optional_float(data, "playbackRate")
        if rate is not None and rate <= 0:
            raise ProtocolError(ErrorCode.BAD_REQUEST, "playbackRate must be positive")
        return cls(
            sequence=_require_int(data, "sequence", minimum=0),
            sample_index=_require_int(data, "sampleIndex", minimum=0),
            pcm_base64=pcm,
            duration_ms=None,
            language=_optional_language(data, "language"),
            encoding=encoding,
            sample_rate=sample_rate,
            channels=channels,
            audio_start_ms=start_ms,
            timeline_epoch=epoch,
            playback_rate=rate,
            final=bool(data.get("final")),
        )

    @property
    def declared_bytes(self) -> int:
        """Decoded byte count implied by the base64 payload, without decoding."""
        padding = 0
        for char in reversed(self.pcm_base64):
            if char == "=":
                padding += 1
            else:
                break
        if padding > 2:
            raise ProtocolError(ErrorCode.BAD_REQUEST, "pcm is not valid base64")
        if len(self.pcm_base64) % 4 and padding:
            raise ProtocolError(ErrorCode.BAD_REQUEST, "pcm is not valid base64")
        return len(self.pcm_base64) // 4 * 3 - padding


@dataclass(frozen=True, slots=True)
class AudioRequest:
    packets: tuple[AudioPacket, ...]
    flush: bool = False
    stream_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "packets": [packet.to_dict() for packet in self.packets],
            "flush": self.flush,
            "streamComplete": self.stream_complete,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "AudioRequest":
        data = _require_mapping(data, "request")
        raw_packets = data.get("packets") or []
        if not isinstance(raw_packets, list):
            raise ProtocolError(ErrorCode.BAD_REQUEST, "packets must be a list")
        if len(raw_packets) > MAX_PACKETS_PER_REQUEST:
            raise ProtocolError(
                ErrorCode.TOO_LARGE,
                f"at most {MAX_PACKETS_PER_REQUEST} packets per request",
            )
        packets = tuple(AudioPacket.from_dict(item) for item in raw_packets)
        return cls(
            packets=packets,
            flush=bool(data.get("flush")),
            stream_complete=bool(data.get("streamComplete")),
        )


@dataclass(frozen=True, slots=True)
class AudioResponse:
    accepted_samples: int
    accepted_range: tuple[int, int] | None
    buffered_samples: int
    gap_samples: int
    segments: tuple[dict[str, Any], ...]
    language: str
    stream_complete: bool
    engine_warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "acceptedSamples": self.accepted_samples,
            "acceptedRange": list(self.accepted_range) if self.accepted_range else None,
            "bufferedSamples": self.buffered_samples,
            "gapSamples": self.gap_samples,
            "segments": list(self.segments),
            "language": self.language,
            "streamComplete": self.stream_complete,
            "engineWarning": self.engine_warning,
        }


@dataclass(frozen=True, slots=True)
class TranscriptQuery:
    """Poll for segments produced since a given revision."""

    since_revision: int = 0
    wait_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"sinceRevision": self.since_revision, "waitSeconds": self.wait_seconds}

    @classmethod
    def from_dict(cls, data: Any) -> "TranscriptQuery":
        data = _require_mapping(data, "request")
        since = data.get("sinceRevision", 0)
        if isinstance(since, bool) or not isinstance(since, int) or since < 0:
            raise ProtocolError(
                ErrorCode.BAD_REQUEST, "sinceRevision must be a non-negative integer"
            )
        wait = _optional_float(data, "waitSeconds") or 0.0
        if wait < 0 or wait > 25:
            raise ProtocolError(
                ErrorCode.BAD_REQUEST, "waitSeconds must be between 0 and 25"
            )
        return cls(since_revision=since, wait_seconds=wait)


@dataclass(frozen=True, slots=True)
class SegmentView:
    """One caption as the extension should render it.

    ``provisional`` means the segment may still be replaced by a later
    revision of the same ``segment_id``; ``translation_failed`` means show
    ``original`` and do not wait for a translation line.
    """

    segment_id: str
    revision: int
    source_language: str
    original: str
    german: str
    start_ms: int
    end_ms: int
    start_sample: int
    end_sample: int
    words: tuple[dict[str, Any], ...] = ()
    translation_language: str = ""
    translation_backend: str = ""
    translation_failed: bool = False
    provisional: bool = False
    timeline_epoch: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "segmentId": self.segment_id,
            "revision": self.revision,
            "sourceLanguage": self.source_language,
            "original": self.original,
            "german": self.german,
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "startSample": self.start_sample,
            "endSample": self.end_sample,
            "words": list(self.words),
            "translationLanguage": self.translation_language,
            "translationBackend": self.translation_backend,
            "translationFailed": self.translation_failed,
            "provisional": self.provisional,
            "timelineEpoch": self.timeline_epoch,
        }


@dataclass(frozen=True, slots=True)
class TranscriptResponse:
    segments: tuple[SegmentView, ...]
    revision: int
    status: str
    language: str
    progress_ms: int
    translation_backend: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "segments": [segment.to_dict() for segment in self.segments],
            "revision": self.revision,
            "status": self.status,
            "language": self.language,
            "progressMs": self.progress_ms,
            "translationBackend": self.translation_backend,
        }


@dataclass(frozen=True, slots=True)
class ErrorResponse:
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


@dataclass(slots=True)
class BridgeStatus:
    """Mutable bookkeeping the tests inspect without touching the network."""

    sessions: dict[str, Any] = field(default_factory=dict)
    accepted_packets: int = 0
    recognized_segments: int = 0
    translation_failures: int = 0
