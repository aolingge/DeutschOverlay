"""Opt-in MP4 playback through Windows loopback and bundled local models."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from deutsch_overlay.audio import LoopbackSource, SileroSpeechDetector, SpeechSegmenter
from deutsch_overlay.captions import CaptionReducer
from deutsch_overlay.config import Settings
from deutsch_overlay.controller import CaptionController
from deutsch_overlay.engines.local import LocalEngine

from test_real_models import synthesize_wav


pytestmark = pytest.mark.skipif(
    sys.platform != "win32"
    or os.environ.get("DEUTSCH_TEST_REAL_MODELS") != "1"
    or os.environ.get("DEUTSCH_TEST_LIVE_LOOPBACK") != "1",
    reason="set both DEUTSCH_TEST_REAL_MODELS and DEUTSCH_TEST_LIVE_LOOPBACK to 1",
)


@pytest.fixture(scope="module")
def prepared_engine():
    engine = LocalEngine()
    engine.prepare()
    return engine


@pytest.mark.parametrize("language,culture,spoken,expected", [
    ("de", "de-DE", "Guten Morgen, ich lerne heute Deutsch.", "Deutsch"),
    ("en", "en-US", "Hello, I am learning German today.", "German"),
    ("zh", "zh-CN", "你好，我今天正在学习德语。", "德语"),
])
def test_mp4_playback_reaches_german_caption(
    prepared_engine, tmp_path: Path, language: str, culture: str, spoken: str, expected: str,
):
    ffmpeg = shutil.which("ffmpeg")
    ffplay = shutil.which("ffplay")
    if not ffmpeg or not ffplay:
        pytest.skip("FFmpeg and FFplay are required for video playback")

    wav_path = tmp_path / "speech.wav"
    video_path = tmp_path / "speech.mp4"
    synthesize_wav(wav_path, culture, spoken)
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25",
         "-i", str(wav_path), "-shortest", "-c:v", "mpeg4", "-c:a", "aac", str(video_path)],
        check=True, capture_output=True, timeout=30,
    )

    stop = Event()
    recorder_ready = Event()
    playback_error = []

    def play_video():
        if not recorder_ready.wait(10):
            playback_error.append("Loopback recorder did not become ready")
            stop.set()
            return
        try:
            subprocess.run(
                [ffplay, "-nodisp", "-autoexit", "-loglevel", "error", "-i", str(video_path)],
                check=True, capture_output=True, timeout=15,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            playback_error.append(str(exc))
        finally:
            time.sleep(0.6)
            stop.set()

    player = Thread(target=play_video, daemon=True)
    player.start()
    frames = []
    try:
        for frame in LoopbackSource().frames(stop):
            if not frames:
                recorder_ready.set()
            frames.append(frame)
            if len(frames) >= 100:
                stop.set()
                break
    finally:
        stop.set()
        player.join(timeout=17)
    assert not playback_error, playback_error
    assert frames
    peak_rms = max(float(np.sqrt(np.mean(frame * frame))) for frame in frames)
    assert peak_rms > 0.003, f"No video audio reached the default playback loopback; peak RMS={peak_rms}"

    detector = SileroSpeechDetector()
    segmenter = SpeechSegmenter(
        sample_rate=16000, frame_samples=1600, silence_seconds=0.3,
        max_seconds=5.0, voice_detector=detector,
    )
    clips = [clip for frame in frames for clip in segmenter.push(frame)]
    final_clip = segmenter.flush()
    if final_clip is not None:
        clips.append(final_clip)
    assert clips, "Video audio was captured but speech segmentation found no voice"

    started = time.monotonic()
    captions = [
        prepared_engine.process(clip, 16000, 1, f"video-{index}", language)
        for index, clip in enumerate(clips)
    ]
    inference_seconds = time.monotonic() - started
    views = [CaptionReducer(1, compare_original=True).apply(caption) for caption in captions if caption]
    assert views, "Speech was segmented but recognition produced no caption"
    originals = " ".join(view.secondary or view.primary for view in views)
    print(f"video={language} peak_rms={peak_rms:.3f} infer={inference_seconds:.2f}s original={originals!r} german={[view.primary for view in views]!r}")
    assert (expected in originals or (language == "zh" and "德語" in originals)), originals
    assert all(view.primary.strip() for view in views)
    assert all((view.secondary is None) == (language == "de") for view in views)
    german_text = " ".join(view.primary for view in views)
    assert all(word in german_text for word in ("lerne", "heute", "Deutsch")), german_text

    auto_captions = [
        prepared_engine.process(clip, 16000, 1, f"auto-{index}", None)
        for index, clip in enumerate(clips)
    ]
    print(f"video={language} auto_languages={[caption.language if caption else None for caption in auto_captions]}")
    assert any(caption and caption.language == language for caption in auto_captions)

    if language == "de":
        app = QApplication.instance() or QApplication([])

        class RecordedVideoSource:
            def frames(self, _stop):
                for frame in frames:
                    yield frame

        controller = CaptionController(
            source_factory=lambda _device: RecordedVideoSource(),
            local_factory=lambda: prepared_engine,
        )
        displayed = []
        controller.view_changed.connect(displayed.append)
        try:
            assert controller.start(Settings(language_lock="de"))
            for _ in range(300):
                QTest.qWait(20)
                if any("Deutsch" in view.primary for view in displayed):
                    break
            assert any("Deutsch" in view.primary for view in displayed), displayed
        finally:
            controller.stop()
            app.processEvents()
