"""Round-trip tests for the browser bridge protocol and signal plumbing."""

from __future__ import annotations

import base64
import http.client
import json
import math
import socket
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import numpy as np
import pytest

from deutsch_overlay.browser_bridge import AsrBridgeService
from deutsch_overlay.browser_protocol import (
    AudioPacket,
    AudioRequest,
    CaptionAvailability,
    ErrorCode,
    ProtocolError,
    SessionRequest,
    SourceKind,
    TranscriptQuery,
)
from deutsch_overlay.browser_server import BridgeServer, BridgeServerConfig
from deutsch_overlay.engines.local import LocalEngine
from deutsch_overlay.pcm import (
    AudioTimeline,
    Resampler,
    TimelineAnchor,
    decode_packet,
    decode_pcm_s16le,
)
from deutsch_overlay.transcript import TranscriptResult, TranscriptSegment

FRAME = 1600


# --------------------------------------------------------------------- helpers


def tone(seconds: float, frequency: float = 440.0, rate: int = 16000, amplitude: float = 0.2, offset: float = 0.0):
    count = int(round(seconds * rate))
    positions = np.arange(count, dtype=np.float64) / rate + offset
    wave = amplitude * np.sin(2 * math.pi * frequency * positions)
    return np.asarray(wave, dtype=np.float32)


def to_payload(samples: np.ndarray, *, channels: int = 1, rate: int = 16000) -> str:
    clipped = np.clip(samples, -1.0, 1.0)
    raw = (clipped * 32767.0).astype("<i2")
    if channels == 2:
        raw = np.repeat(raw[:, None], 2, axis=1).reshape(-1)
    return base64.b64encode(raw.tobytes()).decode("ascii")


def packets_for(samples: np.ndarray, *, rate: int = 16000, chunk: int = FRAME, start_index: int = 0, **extra):
    packets = []
    sequence = extra.pop("sequence_start", 0)
    for offset in range(0, len(samples), chunk):
        block = samples[offset : offset + chunk]
        if not len(block):
            continue
        packets.append(
            AudioPacket(
                sequence=sequence,
                sample_index=start_index + offset,
                pcm_base64=to_payload(block, rate=rate),
                sample_rate=rate,
                **extra,
            )
        )
        sequence += 1
    return packets


class FakeAsr:
    """Stands in for faster-whisper: one segment per clip, timed from its length."""

    def __init__(self, text="Hallo Welt", language="de", words=False):
        self.text = text
        self.language = language
        self.want_words = words
        self.calls = 0

    def transcribe(self, audio, **options):
        self.calls += 1
        duration = len(audio) / 16000
        words = None
        if self.want_words:
            middle = min(0.3, duration)
            words = [SimpleNamespace(**w) for w in (
                dict(word="Hallo", start=0.0, end=middle, probability=0.9),
                dict(word="Welt", start=middle, end=duration, probability=0.9),
            )]
        segment = SimpleNamespace(
            start=0.0,
            end=duration,
            text=self.text,
            no_speech_prob=0.01,
            avg_logprob=-0.2,
            words=words,
        )
        info = SimpleNamespace(
            language=self.language,
            language_probability=0.99,
            duration=duration,
        )
        return [segment], info


class FakeTranslator:
    def __init__(self, text="Hallo Welt", raises=False, empty=False):
        self.text = text
        self.raises = raises
        self.empty = empty
        self.seen: list[str] = []

    def translate(self, text):
        self.seen.append(text)
        if self.raises:
            raise RuntimeError("model exploded")
        return "" if self.empty else self.text


def make_engine(asr=None, translators=None, **kw):
    return LocalEngine(
        asr=asr or FakeAsr(), translators=translators or {}, prefer_gpu=False, **kw
    )


class PreparedEngine:
    """``LocalEngine`` with its model loading replaced by the fakes above.

    The bridge asks the engine to prepare before it accepts audio, and preparing
    the real engine would need faster-whisper and the Opus models on disk. The
    transcription and translation behaviour stays identical; only the "load the
    models" step is dropped.
    """

    name = "prepared-fake"

    def __init__(self, asr=None, translators=None, **kw):
        self.inner = LocalEngine(
            asr=asr or FakeAsr(), translators=translators or {}, prefer_gpu=False, **kw
        )
        self.prepare_calls = 0

    def prepare(self):
        self.prepare_calls += 1

    def __getattr__(self, item):
        return getattr(self.inner, item)


def new_session_payload(**overrides):
    payload = {
        "platform": "bilibili",
        "videoKey": "BV1xx411c7mD",
        "captionAvailability": CaptionAvailability.ABSENT,
        "sourceKind": SourceKind.TAB_CAPTURE,
        "sourceLanguage": "zh",
        "audioStartMs": 0,
        "playbackRate": 1.0,
        "title": "测试视频",
        "url": "https://www.bilibili.com/video/BV1xx411c7mD",
        "client": "yt-dual-subs/test",
    }
    payload.update(overrides)
    return payload


def feed(session, service, packets, **extra):
    """Submit packets the way the extension must: at most 16 per request."""
    if not packets:
        service.feed(session, AudioRequest.from_dict({"packets": [], **extra}))
        return
    for offset in range(0, len(packets), 16):
        batch = packets[offset : offset + 16]
        service.feed(
            session,
            AudioRequest.from_dict({"packets": [p.to_dict() for p in batch], **extra}),
        )


def settle(service, session, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session.job_lock:
            pending = len(session.jobs)
        if pending == 0 and not session.busy:
            return True
        time.sleep(0.02)
    return False


# ------------------------------------------------------------------- protocol


def test_session_request_rejects_missing_start_time_for_tab_capture():
    payload = new_session_payload()
    payload.pop("audioStartMs")
    with pytest.raises(ProtocolError) as caught:
        SessionRequest.from_dict(payload)
    assert caught.value.code == ErrorCode.BAD_REQUEST
    assert "audioStartMs" in caught.value.message


def test_session_request_rejects_unknown_protocol_version():
    payload = new_session_payload(protocolVersion=99)
    with pytest.raises(ProtocolError) as caught:
        SessionRequest.from_dict(payload)
    assert caught.value.code == ErrorCode.UNSUPPORTED_VERSION


def test_session_request_requires_a_known_source_language():
    with pytest.raises(ProtocolError) as caught:
        SessionRequest.from_dict(new_session_payload(sourceLanguage="fr"))
    assert caught.value.code == ErrorCode.LANGUAGE_UNSUPPORTED


def test_audio_request_round_trips_and_limits_packet_count():
    request = AudioRequest.from_dict(
        {
            "packets": [
                {
                    "sequence": 0,
                    "sampleIndex": 0,
                    "pcm": to_payload(tone(0.1)),
                    "sampleRate": 16000,
                }
            ],
            "flush": True,
        }
    )
    assert request.flush is True
    assert request.packets[0].sample_index == 0
    assert request.packets[0].declared_bytes == 3200
    with pytest.raises(ProtocolError) as caught:
        AudioRequest.from_dict(
            {
                "packets": [
                    {"sequence": i, "sampleIndex": i, "pcm": to_payload(tone(0.5)), "sampleRate": 16000}
                    for i in range(64)
                ]
            }
        )
    assert caught.value.code == ErrorCode.TOO_LARGE


def test_audio_request_rejects_oversize_payload_and_bad_base64():
    with pytest.raises(ProtocolError) as caught:
        AudioRequest.from_dict(
            {"packets": [{"sequence": 0, "sampleIndex": 0, "pcm": "A" * (1 << 20 | 1), "sampleRate": 16000}]}
        )
    assert caught.value.code == ErrorCode.TOO_LARGE
    with pytest.raises(ProtocolError):
        decode_pcm_s16le("!!!!")


def test_audio_request_rejects_odd_byte_count():
    payload = base64.b64encode(b"\x01\x02\x03").decode("ascii")
    with pytest.raises(ProtocolError) as caught:
        decode_pcm_s16le(payload)
    assert "odd byte count" in caught.value.message


def test_transcript_query_bounds_wait():
    assert TranscriptQuery.from_dict({"sinceRevision": 3}).since_revision == 3
    with pytest.raises(ProtocolError):
        TranscriptQuery.from_dict({"waitSeconds": 60})


# ---------------------------------------------------------------------- pcm


def test_decode_downmixes_stereo_to_mono():
    stereo = np.array([1000, 3000, -1000, -3000], dtype="<i2")
    mono = decode_packet(base64.b64encode(stereo.tobytes()).decode("ascii"), 2)
    assert mono.shape == (2,)
    assert mono[0] == pytest.approx(2000 / 32768.0, abs=1e-6)


def test_resampler_output_count_is_exact_across_chunking():
    for rate in (16000, 32000, 44100, 48000):
        for chunk in (160, 1000, 4096, 16000):
            source = tone(1.0, rate=rate)
            single = Resampler(rate, 16000)
            whole = single.process(source)
            streamed = Resampler(rate, 16000)
            blocks = []
            for offset in range(0, len(source), chunk):
                blocks.append(streamed.process(source[offset : offset + chunk]))
            joined = np.concatenate(blocks)
            assert joined.size == whole.size == single.expected_output_total(rate)
            assert np.allclose(joined, whole, atol=1e-6)
            # Same total when the stream is closed out with flush().
            flushed = Resampler(rate, 16000)
            pieces = [
                flushed.process(source[offset : offset + chunk])
                for offset in range(0, len(source), chunk)
            ]
            pieces.append(flushed.flush())
            assert np.concatenate(pieces).size == whole.size


def test_resampler_preserves_a_clean_tone_without_phase_shift():
    source = np.sin(2 * math.pi * 440 * np.arange(48000) / 48000).astype(np.float32)
    output = Resampler(48000, 16000).process(source)
    reference = np.sin(2 * math.pi * 440 * np.arange(output.size) / 16000).astype(np.float32)
    correlation = float(np.corrcoef(output[200:-200], reference[200:-200])[0, 1])
    assert correlation > 0.99


def test_resampler_identity_path_is_lossless():
    source = tone(0.5)
    assert np.array_equal(Resampler(16000, 16000).process(source), source)


def test_resampler_flush_is_idempotent():
    resampler = Resampler(44100, 16000)
    resampler.process(tone(0.05, rate=44100))
    assert resampler.flush().size >= 0
    assert resampler.flush().size == 0


def test_timeline_maps_samples_to_media_time_without_a_wall_clock():
    timeline = AudioTimeline()
    assert timeline.media_ms(0) != timeline.media_ms(0)  # nan before any anchor
    timeline.add_anchor(TimelineAnchor(sample_index=0, audio_start_ms=1000, observed_at=0.0))
    assert timeline.media_ms(0) == pytest.approx(1000.0)
    assert timeline.media_ms(16000) == pytest.approx(2000.0)
    timeline.add_anchor(TimelineAnchor(sample_index=8000, audio_start_ms=2000, observed_at=0.0))
    assert timeline.media_ms(12000) == pytest.approx(2250.0)
    timeline.rebase(TimelineAnchor(sample_index=48000, audio_start_ms=90000, observed_at=0.0))
    assert timeline.media_ms(48000) == pytest.approx(90000.0)
    # Before the anchor there is no evidence the audio was still the same
    # timeline, so the map clamps to the anchor instead of extrapolating back.
    assert timeline.media_ms(30000) == pytest.approx(90000.0)


# -------------------------------------------------------------------- engine


def test_transcribe_segments_returns_model_times_not_clock_times():
    engine = make_engine(asr=FakeAsr(text="你好 世界", language="zh"))
    result = engine.transcribe_segments(tone(2.0), offset_samples=32000)
    assert isinstance(result, TranscriptResult)
    assert result.language == "zh"
    segment = result.segments[0]
    assert (segment.start_ms, segment.end_ms) == (0, 2000)
    assert segment.start_sample == 32000
    assert segment.end_sample == 32000 + 32000
    assert segment.text == "你好 世界"


def test_transcribe_segments_keeps_word_times_when_requested():
    engine = make_engine(asr=FakeAsr(words=True))
    result = engine.transcribe_segments(tone(1.0), offset_samples=16000, word_timestamps=True)
    words = result.segments[0].words
    assert [word.text for word in words] == ["Hallo", "Welt"]
    assert words[0].start_sample == 16000
    assert result.segments[0].has_model_words


def test_transcribe_audio_reports_no_translation():
    engine = make_engine(asr=FakeAsr(text="Bonjour", language="en"))
    result = engine.transcribe_audio(tone(1.0))
    assert result.text == "Bonjour"
    assert engine.translators == {}  # pure transcription must not load a translation model


def test_translate_keeps_original_text_when_the_model_fails():
    engine = make_engine(translators={"zh": FakeTranslator(raises=True)})
    translation = engine.translate("你好", "zh")
    assert translation.failed is True
    assert translation.text == "你好"
    assert "翻译失败" in engine.warning


def test_translate_marks_de_as_identity_not_failure():
    engine = make_engine()
    translation = engine.translate("Guten Tag", "de")
    assert translation.failed is False
    assert translation.text == "Guten Tag"
    assert translation.backend == "identity"


def test_process_still_drops_untranslatable_segments():
    engine = make_engine(asr=FakeAsr(text="你好", language="zh"), translators={"zh": FakeTranslator(empty=True)})
    assert engine.process(tone(1.0), 16000, 1, "seg", "auto") is None


# ------------------------------------------------------------------- service


def make_service(**kwargs):
    engine = kwargs.pop("engine", None)
    if engine is None:
        engine = PreparedEngine(**kwargs.pop("engine_kwargs", {}))
    return AsrBridgeService(engine, **kwargs)


def test_captions_present_never_starts_recognition():
    service = make_service()
    response = service.create_session(
        SessionRequest.from_dict(new_session_payload(captionAvailability=CaptionAvailability.PRESENT))
    )
    assert response.recognize is False
    assert response.reason == "captions_present"
    session = service.get(response.session_id)
    assert session.worker is None
    with pytest.raises(ProtocolError) as caught:
        service.feed(session, AudioRequest.from_dict({"packets": []}))
    assert caught.value.code == ErrorCode.CAPTIONS_PRESENT


def test_unknown_caption_state_is_treated_as_having_captions():
    service = make_service()
    response = service.create_session(
        SessionRequest.from_dict(new_session_payload(captionAvailability=CaptionAvailability.UNKNOWN))
    )
    assert response.recognize is False
    assert response.reason == "captions_unknown"


def test_video_without_audio_track_is_refused():
    service = make_service()
    response = service.create_session(
        SessionRequest.from_dict(new_session_payload(hasAudioTrack=False))
    )
    assert response.reason == "no_audio_track"


def test_caption_times_come_from_the_anchor_not_from_recognition_time():
    service = make_service()
    session_response = service.create_session(SessionRequest.from_dict(new_session_payload(audioStartMs=60000)))
    session = service.get(session_response.session_id)
    audio = tone(0.7)
    before = time.monotonic()
    service.feed(session, AudioRequest.from_dict({"packets": [p.to_dict() for p in packets_for(audio)]}))
    service.feed(session, AudioRequest.from_dict({"packets": [], "flush": True}))
    assert settle(service, session)
    segments, _ = session.segments_since(0)
    assert segments, "expected at least one caption"
    first = segments[0]
    assert first.start_ms >= 60000
    assert first.end_ms <= 62000
    # Moving the published clock moves the caption; waiting does not.
    assert time.monotonic() - before < 5.0
    session2_response = service.create_session(SessionRequest.from_dict(new_session_payload(audioStartMs=5000)))
    session2 = service.get(session2_response.session_id)
    service.feed(session2, AudioRequest.from_dict({"packets": [p.to_dict() for p in packets_for(audio)]}))
    service.feed(session2, AudioRequest.from_dict({"packets": [], "flush": True}))
    assert settle(service, session2)
    other, _ = session2.segments_since(0)
    assert other[0].start_ms >= 5000
    assert other[0].start_ms < 60000


def test_translation_revision_reuses_the_same_segment_id():
    service = make_service(engine_kwargs={"translators": {"zh": FakeTranslator("Hallo Welt")}})
    response = service.create_session(SessionRequest.from_dict(new_session_payload(sourceLanguage="zh")))
    session = service.get(response.session_id)
    service.feed(session, AudioRequest.from_dict({"packets": [p.to_dict() for p in packets_for(tone(0.7))]}))
    service.feed(session, AudioRequest.from_dict({"packets": [], "flush": True}))
    assert settle(service, session)
    segments, revision = session.segments_since(0)
    assert len(segments) == 1
    view = segments[0]
    assert view.original == "Hallo Welt"
    assert view.german == "Hallo Welt"
    assert view.translation_failed is False
    assert view.translation_backend == "opus-zh-de"
    assert view.revision == revision
    assert session.status() in ("ready", "recognizing")


def test_failed_translation_still_publishes_the_original():
    service = make_service(
        engine_kwargs={"asr": FakeAsr(text="你好", language="zh"), "translators": {"zh": FakeTranslator(raises=True)}}
    )
    response = service.create_session(SessionRequest.from_dict(new_session_payload(sourceLanguage="zh")))
    session = service.get(response.session_id)
    service.feed(session, AudioRequest.from_dict({"packets": [p.to_dict() for p in packets_for(tone(0.7))]}))
    service.feed(session, AudioRequest.from_dict({"packets": [], "flush": True}))
    assert settle(service, session)
    segments, _ = session.segments_since(0)
    assert segments[0].original == "你好"
    assert segments[0].german == "你好"
    assert segments[0].translation_failed is True
    assert service.status.translation_failures == 1


def test_gap_larger_than_tolerance_starts_a_new_stream_and_is_counted():
    service = make_service()
    response = service.create_session(SessionRequest.from_dict(new_session_payload(audioStartMs=0)))
    session = service.get(response.session_id)
    service.feed(session, AudioRequest.from_dict({"packets": [p.to_dict() for p in packets_for(tone(0.5))]}))
    assert session.next_sample == 8000
    # Jump forward by 5 s of audio: cannot be spliced onto what came before,
    # so the stream restarts at 0 and keeps its media time from the anchor.
    jumped = packets_for(tone(0.5), start_index=8000 + 80000, sequence_start=10, audio_start_ms=88000)
    service.feed(session, AudioRequest.from_dict({"packets": [p.to_dict() for p in jumped]}))
    assert session.next_sample == 8000  # relative to the new stream, not the jump
    assert session.gap_samples == 0  # a seek is not a gap
    assert session.timeline.media_ms(0) == pytest.approx(88000.0)
    assert session.timeline.media_ms(8000) == pytest.approx(88500.0)
    assert session.timeline_epoch == 0  # no epoch was declared, so none invented


def test_small_hole_is_recorded_as_a_gap_but_keeps_the_stream_continuous():
    service = make_service()
    response = service.create_session(SessionRequest.from_dict(new_session_payload()))
    session = service.get(response.session_id)
    packets = packets_for(tone(0.5))
    service.feed(session, AudioRequest.from_dict({"packets": [p.to_dict() for p in packets[:2]]}))
    # 1 ms of audio (16 samples) is missing but well under the seek tolerance.
    hole = packets_for(tone(0.5), start_index=3200 + 16, sequence_start=2, chunk=1600)
    service.feed(session, AudioRequest.from_dict({"packets": [p.to_dict() for p in hole]}))
    assert session.gap_samples == 16
    assert session.next_sample == 3200 + 16 + 8000
    assert session.timeline_epoch == 0


def test_duplicate_and_out_of_order_packets_are_ignored():
    service = make_service()
    response = service.create_session(SessionRequest.from_dict(new_session_payload()))
    session = service.get(response.session_id)
    packets = packets_for(tone(0.5))
    service.feed(session, AudioRequest.from_dict({"packets": [p.to_dict() for p in packets]}))
    first_count = session.accepted_packets
    service.feed(session, AudioRequest.from_dict({"packets": [p.to_dict() for p in packets]}))
    assert session.accepted_packets == first_count
    assert session.next_sample == 8000


def test_new_timeline_epoch_rebases_instead_of_shifting():
    service = make_service()
    response = service.create_session(
        SessionRequest.from_dict(new_session_payload(audioStartMs=1000, timelineEpoch=1))
    )
    session = service.get(response.session_id)
    service.feed(
        session,
        AudioRequest.from_dict(
            {"packets": [p.to_dict() for p in packets_for(tone(0.5), timeline_epoch=1)]}
        ),
    )
    service.feed(session, AudioRequest.from_dict({"packets": [], "flush": True}))
    assert settle(service, session)
    first = session.all_segments()
    assert first and first[0].timeline_epoch == 1
    assert first[0].start_ms == pytest.approx(1000.0)
    # The viewer seeks: the page restarts its sample counter at 0 and publishes
    # the new media position on a new epoch. Its packet sequence keeps rising,
    # because the sequence numbers a retry and a duplicate, not the stream.
    seeking = packets_for(
        tone(0.5),
        timeline_epoch=2,
        audio_start_ms=300000,
        sequence_start=session.last_sequence + 1,
    )
    service.feed(
        session,
        AudioRequest.from_dict(
            {"packets": [p.to_dict() for p in seeking], "streamComplete": True}
        ),
    )
    assert settle(service, session)
    later = [segment for segment in session.all_segments() if segment.timeline_epoch == 2]
    assert later, "the seek should produce captions on the new epoch"
    assert later[0].start_sample == 0
    assert later[0].start_ms == pytest.approx(300000.0, abs=1)
    assert later[0].end_ms == pytest.approx(300500.0, abs=1)
    # Seeking again while the stream was still open must be accepted.
    assert session.stream_complete is True
    again = packets_for(
        tone(0.5),
        timeline_epoch=3,
        audio_start_ms=120000,
        sequence_start=session.last_sequence + 1,
    )
    service.feed(session, AudioRequest.from_dict({"packets": [p.to_dict() for p in again], "streamComplete": True}))
    assert settle(service, session)
    newest = [segment for segment in session.all_segments() if segment.timeline_epoch == 3]
    assert newest and newest[0].start_ms == pytest.approx(120000.0, abs=1)


def test_session_cap_returns_engine_busy():
    service = make_service(max_sessions=1)
    service.create_session(SessionRequest.from_dict(new_session_payload()))
    with pytest.raises(ProtocolError) as caught:
        service.create_session(SessionRequest.from_dict(new_session_payload()))
    assert caught.value.code == ErrorCode.ENGINE_BUSY


def test_stream_complete_flushes_the_tail_utterance():
    service = make_service()
    response = service.create_session(SessionRequest.from_dict(new_session_payload()))
    session = service.get(response.session_id)
    audio = tone(0.4)  # shorter than any silence threshold
    service.feed(
        session,
        AudioRequest.from_dict({"packets": [p.to_dict() for p in packets_for(audio)], "streamComplete": True}),
    )
    assert settle(service, session)
    assert session.stream_complete is True
    segments, _ = session.segments_since(0)
    assert segments, "the remaining utterance must not be dropped"
    assert session.status() == "ready"
    with pytest.raises(ProtocolError):
        service.feed(session, AudioRequest.from_dict({"packets": [p.to_dict() for p in packets_for(audio)]}))


def test_empty_speech_produces_no_captions():
    service = make_service()
    response = service.create_session(SessionRequest.from_dict(new_session_payload()))
    session = service.get(response.session_id)
    silence = np.zeros(16000, dtype=np.float32)
    service.feed(session, AudioRequest.from_dict({"packets": [p.to_dict() for p in packets_for(silence)]}))
    service.feed(session, AudioRequest.from_dict({"packets": [], "streamComplete": True}))
    assert settle(service, session)
    segments, _ = session.segments_since(0)
    assert segments == ()


def test_long_utterance_is_cut_into_bounded_clips_without_losing_time():
    service = make_service()
    response = service.create_session(SessionRequest.from_dict(new_session_payload()))
    session = service.get(response.session_id)
    audio = tone(16.0)
    feed(session, service, packets_for(audio, chunk=FRAME * 4))
    feed(session, service, [], streamComplete=True)
    assert settle(service, session, timeout=20.0)
    segments, _ = session.segments_since(0)
    assert len(segments) >= 2, "16 s of continuous speech must be cut into several clips"
    starts = [segment.start_sample for segment in segments]
    assert starts == sorted(starts)
    assert all(segment.end_sample - segment.start_sample <= 16000 * 8 for segment in segments)


def test_streaming_in_odd_chunks_matches_one_whole_submission():
    service = make_service()
    audio = tone(2.0)
    whole = service.create_session(SessionRequest.from_dict(new_session_payload()))
    session = service.get(whole.session_id)
    feed(session, service, packets_for(audio, chunk=1234))
    feed(session, service, [], flush=True)
    assert settle(service, session)
    segments, _ = session.segments_since(0)
    assert segments
    assert session.next_sample == len(audio)
    assert segments[0].end_sample <= len(audio)


def test_non_16k_input_is_resampled_before_recognition():
    service = make_service()
    response = service.create_session(SessionRequest.from_dict(new_session_payload()))
    session = service.get(response.session_id)
    audio_48k = tone(1.0, rate=48000)
    packets = [
        AudioPacket(
            sequence=index,
            sample_index=offset,
            pcm_base64=to_payload(audio_48k[offset : offset + 4800], rate=48000),
            sample_rate=48000,
        )
        for index, offset in enumerate(range(0, len(audio_48k), 4800))
    ]
    feed(session, service, packets, streamComplete=True)
    assert settle(service, session)
    segments, _ = session.segments_since(0)
    assert segments
    assert segments[-1].end_sample <= 16000 * 1.01


# ---------------------------------------------------------------------- http


def http_json(url, *, method="GET", body=None, token=None, headers=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def http_feed(bridge, session_id, packets, **extra):
    for offset in range(0, len(packets), 16):
        http_json(
            f"{bridge.url}/v1/session/{session_id}/audio",
            method="POST",
            body={"packets": [p.to_dict() for p in packets[offset : offset + 16]], **extra},
            token=bridge.token,
        )


def _read_raw_response(sock, head):
    """Finish reading one raw HTTP response whose body ``head`` may cut short.

    A refusal that happens before the client has finished sending makes the
    client-side socket abort, so the tests read the reply themselves instead of
    letting urllib raise on a connection that closed early.
    """
    text = head
    length = 0
    for line in text.split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    if "\r\n\r\n" in text:
        body = text.split("\r\n\r\n", 1)[1]
        text = text.split("\r\n\r\n", 1)[0] + "\r\n\r\n"
    else:
        body = ""
    while len(body.encode("utf-8")) < length:
        chunk = sock.recv(4096)
        if not chunk:
            break
        body += chunk.decode("utf-8", "replace")
    return body


@pytest.fixture()
def bridge(tmp_path):
    service = make_service()
    config = BridgeServerConfig(port=0, token="test-token", token_path=tmp_path / "token")
    server = BridgeServer(service, config).start()
    yield server
    server.stop()


def test_health_does_not_need_a_token(bridge):
    status, payload = http_json(f"{bridge.url}/v1/health")
    assert status == 200
    assert payload["protocolVersion"] == 1
    assert payload["service"] == "deutsch-overlay-bridge"


def test_everything_else_needs_the_token(bridge):
    status, payload = http_json(f"{bridge.url}/v1/session", method="POST", body=new_session_payload())
    assert status == 401
    assert payload["error"]["code"] == ErrorCode.UNAUTHORIZED
    status, payload = http_json(
        f"{bridge.url}/v1/session", method="POST", body=new_session_payload(), token="wrong"
    )
    assert status == 401


def test_bad_host_header_is_rejected_as_dns_rebinding(bridge):
    status, payload = http_json(
        f"{bridge.url}/v1/health", headers={"Host": "evil.example.com"}
    )
    assert status == 403
    assert payload["error"]["code"] == ErrorCode.FORBIDDEN_ORIGIN


def test_page_origin_that_is_not_an_extension_is_rejected(bridge):
    status, payload = http_json(
        f"{bridge.url}/v1/health", headers={"Origin": "https://evil.example.com"}
    )
    assert status == 403
    status, payload = http_json(
        f"{bridge.url}/v1/health", headers={"Origin": "chrome-extension://abcdefghijklmnop"}
    )
    assert status == 200


def test_extension_origin_gets_cors_headers_on_real_responses(bridge):
    """A JSON POST with an Authorization header is never a simple request."""
    request = urllib.request.Request(f"{bridge.url}/v1/health", method="GET")
    request.add_header("Origin", "chrome-extension://abcdefghijklmnop")
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        assert (
            response.headers.get("Access-Control-Allow-Origin")
            == "chrome-extension://abcdefghijklmnop"
        )
    # No Origin (a plain loopback client like curl) gets no CORS header at all.
    with urllib.request.urlopen(f"{bridge.url}/v1/health", timeout=10) as response:
        assert response.headers.get("Access-Control-Allow-Origin") is None


def test_preflight_is_answered_for_an_extension_but_not_for_a_page(bridge):
    def preflight(origin):
        parts = bridge.url.split("://", 1)[1]
        host, port = parts.split(":", 1)
        connection = http.client.HTTPConnection(host, int(port), timeout=10)
        try:
            connection.request(
                "OPTIONS",
                "/v1/session",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization,content-type",
                },
            )
            return connection.getresponse()
        finally:
            connection.close()

    response = preflight("chrome-extension://abcdefghijklmnop")
    assert response.status == 204
    assert response.getheader("Access-Control-Allow-Origin") == "chrome-extension://abcdefghijklmnop"
    assert "POST" in response.getheader("Access-Control-Allow-Methods")
    assert "Authorization" in response.getheader("Access-Control-Allow-Headers")
    # A preflight needs no token, but a web page still cannot unlock the bridge.
    response = preflight("https://evil.example.com")
    assert response.status == 403
    assert response.getheader("Access-Control-Allow-Origin") is None


def test_full_http_round_trip_produces_timed_captions(bridge):
    status, session_payload = http_json(
        f"{bridge.url}/v1/session",
        method="POST",
        body=new_session_payload(audioStartMs=120000, sourceLanguage="de"),
        token=bridge.token,
    )
    assert status == 200
    assert session_payload["recognize"] is True
    session_id = session_payload["sessionId"]
    audio = tone(0.7)
    status, audio_payload = http_json(
        f"{bridge.url}/v1/session/{session_id}/audio",
        method="POST",
        body={"packets": [p.to_dict() for p in packets_for(audio)]},
        token=bridge.token,
    )
    assert status == 200
    assert audio_payload["gapSamples"] == 0
    status, finish_payload = http_json(
        f"{bridge.url}/v1/session/{session_id}/audio",
        method="POST",
        body={"packets": [], "streamComplete": True},
        token=bridge.token,
    )
    assert status == 200
    assert finish_payload["streamComplete"] is True
    deadline = time.monotonic() + 10
    segments = []
    while time.monotonic() < deadline:
        status, transcript = http_json(
            f"{bridge.url}/v1/session/{session_id}/transcript",
            method="POST",
            body={"sinceRevision": 0},
            token=bridge.token,
        )
        assert status == 200
        segments = transcript["segments"]
        if segments and transcript["status"] == "ready":
            break
        time.sleep(0.05)
    assert segments, "expected captions over HTTP"
    assert segments[0]["startMs"] >= 120000
    assert segments[0]["startMs"] < 121000
    assert segments[0]["sourceLanguage"] == "de"
    assert segments[0]["endMs"] > segments[0]["startMs"]
    status, closed = http_json(
        f"{bridge.url}/v1/session/{session_id}", method="DELETE", token=bridge.token
    )
    assert status == 200
    assert closed["ok"] is True


def test_http_unknown_session_is_404(bridge):
    status, payload = http_json(
        f"{bridge.url}/v1/session/deadbeef/transcript",
        method="POST",
        body={},
        token=bridge.token,
    )
    assert status == 404
    assert payload["error"]["code"] == ErrorCode.NOT_FOUND


def test_http_oversize_body_is_refused(bridge):
    """An oversized upload must be refused on its Content-Length, before it is read."""
    settings = bridge.config
    port = bridge.httpd.server_address[1]
    with socket.create_connection((settings.host, port), timeout=10) as sock:
        sock.sendall(
            f"POST /v1/session/{'a' * 8}/audio HTTP/1.1\r\n"
            f"Host: {settings.host}:{port}\r\n"
            f"Authorization: Bearer {bridge.token}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {settings.max_body_bytes + 10}\r\n\r\n".encode("ascii")
        )
        head = sock.recv(4096).decode("utf-8", "replace")
        assert head.startswith("HTTP/1.1 413"), head[:200]
        assert ErrorCode.TOO_LARGE in _read_raw_response(sock, head)


def test_concurrent_sessions_do_not_share_state(bridge):
    results = {}

    def run(name, start_ms):
        status, payload = http_json(
            f"{bridge.url}/v1/session",
            method="POST",
            body=new_session_payload(videoKey=name, audioStartMs=start_ms),
            token=bridge.token,
        )
        session_id = payload["sessionId"]
        http_json(
            f"{bridge.url}/v1/session/{session_id}/audio",
            method="POST",
            body={"packets": [p.to_dict() for p in packets_for(tone(0.7))], "streamComplete": True},
            token=bridge.token,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            _, transcript = http_json(
                f"{bridge.url}/v1/session/{session_id}/transcript",
                method="POST",
                body={"sinceRevision": 0},
                token=bridge.token,
            )
            if transcript["segments"] and transcript["status"] == "ready":
                results[name] = transcript["segments"][0]["startMs"]
                return
            time.sleep(0.05)

    threads = [
        threading.Thread(target=run, args=("video-a", 10000)),
        threading.Thread(target=run, args=("video-b", 400000)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert results["video-a"] >= 10000 and results["video-a"] < 20000
    assert results["video-b"] >= 400000 and results["video-b"] < 410000
