"""Opt-in real-time FFplay -> WASAPI -> model -> Windows overlay check."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from deutsch_overlay.app import DesktopApp
from deutsch_overlay.config import Settings, save_settings
from deutsch_overlay.audio import LoopbackSource
from deutsch_overlay.controller import CaptionController
from deutsch_overlay.engines.local import LocalEngine

from test_real_models import synthesize_wav


pytestmark = pytest.mark.skipif(
    sys.platform != "win32"
    or os.environ.get("QT_QPA_PLATFORM") != "windows"
    or os.environ.get("DEUTSCH_TEST_REAL_MODELS") != "1"
    or os.environ.get("DEUTSCH_TEST_LIVE_LOOPBACK") != "1",
    reason="requires real Windows Qt, local models, and live playback",
)


def wait_for_ui(application, seconds: float) -> None:
    # QTest.qWait holds the GIL during this native capture setup.
    time.sleep(seconds)
    application.processEvents()


@pytest.fixture(scope="module")
def prepared_engine():
    engine = LocalEngine()
    engine.prepare()
    return engine


@pytest.mark.parametrize("language,culture,spoken", [
    ("de", "de-DE", "Guten Morgen, ich lerne heute Deutsch."),
    ("zh", "zh-CN", "你好，我今天正在学习德语。"),
])
def test_playing_video_reaches_visible_overlay(tmp_path, prepared_engine, language, culture, spoken):
    ffmpeg, ffplay = shutil.which("ffmpeg"), shutil.which("ffplay")
    if not ffmpeg or not ffplay:
        pytest.skip("FFmpeg and FFplay are required")
    wav, video = tmp_path / "speech.wav", tmp_path / "speech.mp4"
    synthesize_wav(wav, culture, spoken)
    subprocess.run([
        ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=320x180:r=25", "-i", str(wav), "-shortest",
        "-c:v", "mpeg4", "-c:a", "aac", str(video),
    ], check=True, capture_output=True, timeout=30)

    application = QApplication.instance() or QApplication([])
    settings = Settings(language_lock=language, compare_original=True, fade_seconds=20)
    class CountingSource(LoopbackSource):
        frame_count = 0

        def frames(self, stop):
            for frame in super().frames(stop):
                self.frame_count += 1
                yield frame

    source = CountingSource(retry_forever=True)
    controller = CaptionController(source_factory=lambda _device: source, local_factory=lambda: prepared_engine)

    class SilentHotkeys(QObject):
        action = Signal(str)
        failed = Signal(str)

        def start(self):
            pass

        def stop(self):
            pass

    settings_path = tmp_path / "settings.json"
    save_settings(settings_path, settings)
    runtime = DesktopApp(
        application, settings_path=settings_path, controller=controller,
        hotkeys=SilentHotkeys(), devices=[],
    )
    overlay = runtime.overlay
    statuses, views, levels = [], [], []
    controller.status_changed.connect(statuses.append)
    controller.level_changed.connect(levels.append)
    controller.view_changed.connect(views.append)
    player = None
    try:
        runtime.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not any("正在连接电脑播放设备" in value for value in statuses):
            wait_for_ui(application, 0.1)
        assert any("正在连接电脑播放设备" in value for value in statuses), statuses
        wait_for_ui(application, 1.2)
        player = subprocess.Popen(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "error", "-i", str(video)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            wait_for_ui(application, 0.1)
            if any("Deutsch" in view.primary and (language != "zh" or view.secondary) for view in views):
                break
        assert player.wait(timeout=10) == 0
        assert any("Deutsch" in view.primary for view in views), {
            "captions": [view.primary for view in views], "statuses": statuses,
            "levels": [round(value, 4) if isinstance(value, float) else value for value in levels[-20:]],
            "capture_generation": source.capture_generation,
            "frame_count": source.frame_count,
            "active_device": repr(source.active_device),
        }
        assert overlay.isVisible() and "Deutsch" in overlay.primary_label.text()
        if language == "zh":
            assert overlay.secondary_label.isVisible()
            assert any(char in overlay.secondary_label.text() for char in "你好学习學習")
        else:
            assert overlay.secondary_label.isHidden()
        assert not overlay.grab().isNull()
        print(f"live_overlay={language} captions={[view.primary for view in views]}")
    finally:
        if player is not None and player.poll() is None:
            player.terminate()
            player.wait(timeout=5)
        runtime.shutdown()
