"""Behavioral checks for independently scheduled ASR, settings and retry."""

import http.client
import json
import threading
import time

import numpy as np
import pytest

from deutsch_overlay.audio import SpeechSpan
from deutsch_overlay.browser_bridge import AsrBridgeService, BridgeSession
from deutsch_overlay.browser_protocol import ProtocolError, SessionRequest, TranscriptQuery
from deutsch_overlay.browser_server import BridgeServer, BridgeServerConfig
from deutsch_overlay.transcript import TranscriptResult, TranscriptSegment, TranscriptTranslation


class Engine:
    chinese_script = "simplified"
    hotwords = {}
    warning = ""
    _using_gpu = False

    def __init__(self):
        self.translation_started = threading.Event()
        self.release_translation = threading.Event()
        self.release_translation.set()
        self.translation_inputs = []
        self.recognition_inputs = []
        self.fail_translation = False

    def prepare(self):
        pass

    def transcribe_segments(self, samples, **kwargs):
        self.recognition_inputs.append(kwargs)
        start = kwargs["offset_samples"]
        return TranscriptResult(language="en", segments=(TranscriptSegment(
            text=f"Clip {start}.", language="en", start_ms=start // 16,
            end_ms=start // 16 + 1000, start_sample=start, end_sample=start + len(samples),
            avg_logprob=-0.2, no_speech_probability=0.01,
        ),))

    def translate(self, text, language):
        self.translation_inputs.append(text)
        self.translation_started.set()
        assert self.release_translation.wait(5)
        if self.fail_translation:
            raise RuntimeError("private secret should not be exposed")
        return TranscriptTranslation(language="de", text=f"DE: {text}", backend="test")


def request(**kwargs):
    return SessionRequest(platform="youtube", video_key="fixture", caption_availability="absent",
        source_language="en", translation_target="de", audio_start_ms=0, timeline_epoch=0, **kwargs)


@pytest.fixture
def running():
    engine = Engine()
    service = AsrBridgeService(engine)
    session = service.get(service.create_session(request()).session_id)
    try:
        yield service, session, engine
    finally:
        engine.release_translation.set()
        service.close_all()


def enqueue(service, session, start=0):
    with session.state_lock:
        service._enqueue(session, SpeechSpan(np.ones(16000, dtype=np.float32), start, start + 16000), provisional=False)
    session.wake.set()


def eventually(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_slow_translation_does_not_block_next_recognition_and_idle_waits_for_both(running):
    service, session, engine = running
    engine.release_translation.clear()
    enqueue(service, session)
    assert engine.translation_started.wait(2)
    enqueue(service, session, 16000)
    eventually(lambda: len(session.all_segments()) == 2)
    assert not session.idle.is_set()
    assert all(not s.german for s in session.all_segments())
    engine.release_translation.set()
    assert session.idle.wait(3)
    assert all(s.german for s in session.all_segments())


@pytest.mark.parametrize("explicit_epoch", [False, True])
def test_translation_result_after_seek_cannot_revise_old_caption(running, explicit_epoch):
    service, session, engine = running
    engine.release_translation.clear()
    enqueue(service, session)
    assert engine.translation_started.wait(2)
    revision = session.revision
    with session.state_lock:
        service._reset_audio_state(session)
        if explicit_epoch:
            session.timeline_epoch += 1
    engine.release_translation.set()
    assert session.idle.wait(3)
    assert session.revision == revision
    assert session.all_segments()[0].german == ""


def test_translation_exception_preserves_original_and_worker_processes_next_clip(running):
    service, session, engine = running
    engine.fail_translation = True
    enqueue(service, session)
    assert session.idle.wait(3)
    view = session.all_segments()[0]
    assert view.translation_failed and view.german == view.original
    engine.fail_translation = False
    enqueue(service, session, 16000)
    assert session.idle.wait(3)
    assert not session.all_segments()[-1].translation_failed
    assert "private secret" not in json.dumps(service.transcripts(session, TranscriptQuery()).to_dict())


def test_confidence_is_qualitative_preserved_through_translation(running):
    service, session, _ = running
    enqueue(service, session)
    assert session.idle.wait(3)
    view = session.all_segments()[0].to_dict()
    assert view["avgLogprob"] == -0.2 and view["noSpeechProbability"] == 0.01
    assert view["uncertain"] is False and view["uncertaintyReasons"] == []
    with session.state_lock:
        service._publish(session, TranscriptResult(language="en", segments=(TranscriptSegment(
            text="Uncertain", language="en", start_ms=0, end_ms=1,
            start_sample=100, end_sample=101, avg_logprob=-1.5,
        ),)), provisional=False)
    assert session.idle.wait(3)
    value = session.all_segments()[-1].to_dict()
    assert value["uncertain"] is True
    assert set(value["uncertaintyReasons"]) == {"scores_unavailable", "low_log_probability"}


def test_recognition_and_translation_queues_bound_memory_and_report_drops():
    engine = Engine()
    service = AsrBridgeService(engine, max_clip_jobs=2, max_translation_jobs=2)
    session = BridgeSession("fixture", request(), True, "no_captions", "test")
    for index in range(5):
        enqueue(service, session, index * 16000)
        service._publish(session, TranscriptResult(language="en", segments=(TranscriptSegment(
            text=f"Text {index}", language="en", start_ms=index, end_ms=index + 1,
            start_sample=index, end_sample=index + 1,
        ),)), provisional=False)
    assert len(session.jobs) == 2 and len(session.translation_jobs) == 2
    assert session.metrics["droppedClips"] == session.metrics["droppedTranslations"] == 3
    assert all(s.translation_failed for s in session.all_segments()[:3])
    assert session.warning


def test_optional_context_joins_fragments_without_modifying_source(running):
    service, session, engine = running
    service.translation_context = True
    session.stream_complete = True
    segments = (
        TranscriptSegment("This is", "en", 0, 500, 0, 8000),
        TranscriptSegment("one sentence.", "en", 500, 1000, 8000, 16000),
        TranscriptSegment("Another sentence.", "en", 1000, 1500, 16000, 24000),
    )
    with session.state_lock:
        service._publish(session, TranscriptResult(language="en", segments=segments), provisional=False)
    assert session.idle.wait(3)
    assert engine.translation_inputs == ["This is one sentence.", "Another sentence."]
    views = session.all_segments()
    assert [v.original for v in views] == [s.text for s in segments]
    assert views[0].german == views[1].german
    assert views[0].translation_group_ids == (views[0].segment_id, views[1].segment_id)
    assert views[2].translation_group_ids == ()


def test_retry_retains_bounded_audio_reuses_id_and_translation_cache(running):
    service, session, engine = running
    enqueue(service, session)
    assert session.idle.wait(3)
    view = session.all_segments()[0]
    result = service.retry_segment(session, {"segmentId": view.segment_id, "timelineEpoch": 0})
    assert result["queued"]
    assert session.idle.wait(3)
    assert len(session.all_segments()) == 1
    assert engine.recognition_inputs[-1]["beam_size"] == 5
    assert session.metrics["translationCacheHits"] == 1
    for index in range(1, 10):
        enqueue(service, session, index * 16000)
        assert session.idle.wait(3)
    assert len(session.retained_clips) == 8
    with pytest.raises(ProtocolError, match="过期"):
        service.retry_segment(session, {"segmentId": view.segment_id, "timelineEpoch": 0})
    with pytest.raises(ProtocolError, match="过期"):
        service.retry_segment(session, {"segmentId": session.all_segments()[-1].segment_id, "timelineEpoch": 1})


def test_settings_reject_active_sessions_and_validate_atomically(running):
    service, session, engine = running
    initial = service.settings()["settings"]
    with pytest.raises(ProtocolError) as error:
        service.update_settings({"chineseScript": "traditional"})
    assert error.value.code == "conflict"
    service.close_session(session.session_id)
    with pytest.raises(ProtocolError):
        service.update_settings({"chineseScript": "traditional", "hotwords": {"en": "bad\nsecret"}})
    assert service.settings()["settings"] == initial
    changed = service.update_settings({"chineseScript": "traditional", "hotwords": {"de": "CTranslate2"}, "translationContext": True})
    assert changed["settings"]["translationContext"] is True
    assert changed["settings"]["hotwords"] == {"de": "CTranslate2", "en": "", "zh": ""}
    assert "CTranslate2" not in json.dumps(service.health())
    with pytest.raises(ProtocolError):
        service.update_settings({"modelProfile": "C:/secret/model"})


def test_settings_reject_workers_still_draining_after_session_close(running):
    service, session, engine = running
    engine.release_translation.clear()
    enqueue(service, session)
    assert engine.translation_started.wait(2)
    service.close_session(session.session_id)
    revision = session.revision
    with pytest.raises(ProtocolError) as error:
        service.update_settings({"translationContext": True})
    assert error.value.code == "conflict"
    engine.release_translation.set()
    eventually(lambda: not session.translation_busy)
    assert session.revision == revision
    assert service.update_settings({"translationContext": True})["ok"]


def test_settings_and_retry_http_require_authentication_and_reject_bad_origin():
    service = AsrBridgeService(Engine())
    with BridgeServer(service, BridgeServerConfig(port=0, token="fixture-token")) as server:
        port = server.httpd.config.port

        def call(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request(method, path, json.dumps(body or {}), headers or {})
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
            return response.status, payload

        assert call("GET", "/v1/settings")[0] == 401
        assert call("POST", "/v1/settings", {"hotwords": {"en": "private term"}})[0] == 401
        auth = {"Authorization": "Bearer fixture-token", "Origin": "chrome-extension://fixture"}
        assert call("GET", "/v1/settings", headers=auth)[0] == 200
        assert call("POST", "/v1/settings", {"hotwords": {"en": "private term"}}, auth)[0] == 200
        assert "private term" not in json.dumps(call("GET", "/v1/health")[1])
        assert call("POST", "/v1/settings", {"hotwords": {"en": "private\nterm"}}, auth)[0] == 400
        assert call("GET", "/v1/settings", headers={**auth, "Origin": "https://example.com"})[0] == 403
        assert call("POST", "/v1/session/" + "a" * 32 + "/retry")[0] == 401


def test_prepare_failure_never_leaks_filesystem_or_terms():
    engine = Engine()
    engine.prepare = lambda: (_ for _ in ()).throw(RuntimeError("C:/secret/path private hotword"))
    service = AsrBridgeService(engine)
    service.prepare()
    assert not service.health()["modelReady"]
    assert "secret" not in service.health()["warning"]


def test_model_profiles_select_only_registered_local_paths(tmp_path):
    from deutsch_overlay.engines.local import LocalEngine

    for name in ("model.bin", "config.json", "tokenizer.json"):
        (tmp_path / name).write_text("fixture")
    engine = LocalEngine(prefer_gpu=False)
    service = AsrBridgeService(engine, model_profiles={"small": None, "turbo": tmp_path})
    settings = service.update_settings({"modelProfile": "turbo", "hotwords": {"en": "CTranslate2"}})
    assert settings["settings"]["modelProfile"] == "turbo"
    assert service.engine.asr_model_path == tmp_path
    assert service.engine.asr is None and not settings["modelReady"]
    assert str(tmp_path) not in json.dumps(settings)
    assert service.engine.hotwords == {"en": "CTranslate2"}
    (tmp_path / "model.bin").unlink()
    service.update_settings({"modelProfile": "small"})
    before = service.settings()["settings"]
    with pytest.raises(ProtocolError):
        service.update_settings({"modelProfile": "turbo", "hotwords": {"en": "changed"}})
    assert service.settings()["settings"]["modelProfile"] == "small"
    assert service.settings()["settings"] == before


def test_latency_metrics_keep_only_recent_samples(running):
    service, session, _ = running
    for value in range(256):
        service._record_latency(session, "recognition", value)
    assert len(session.latency_samples["recognition"]) == 128
    assert session.metrics["recognitionMs"] == 255
    assert session.metrics["recognitionP95Ms"] == 249


@pytest.mark.parametrize("remaining", [0, 1])
def test_retry_fewer_sentences_emits_tombstones_for_surplus_slots(running, remaining):
    service, session, engine = running
    original = engine.transcribe_segments

    def two_sentences(samples, **kwargs):
        result = original(samples, **kwargs)
        first = result.segments[0]
        from dataclasses import replace
        return replace(result, segments=(first, replace(first, text="Second sentence.")))

    engine.transcribe_segments = two_sentences
    enqueue(service, session)
    assert session.idle.wait(3)
    before = session.all_segments()
    revision = session.revision

    def fewer_sentences(samples, **kwargs):
        result = original(samples, **kwargs)
        from dataclasses import replace
        return replace(result, segments=result.segments[:remaining])

    engine.transcribe_segments = fewer_sentences
    service.retry_segment(session, {"segmentId": before[0].segment_id, "timelineEpoch": 0})
    assert session.idle.wait(3)
    assert len(session.all_segments()) == remaining
    changes, _ = session.segments_since(revision)
    removed = [view.to_dict() for view in changes if view.removed]
    assert len(removed) == 2 - remaining
    assert all(view["removed"] is True and view["words"] == [] for view in removed)
