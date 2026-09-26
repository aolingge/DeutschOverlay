"""Opt-in Windows SAPI speech through the actual bundled local models."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import wave
from pathlib import Path
from threading import Event, Thread

import numpy as np
import pytest

from deutsch_overlay.captions import CaptionReducer
from deutsch_overlay.audio import LoopbackSource, SileroSpeechDetector, SpeechSegmenter
from deutsch_overlay.engines.local import LocalEngine


pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("DEUTSCH_TEST_REAL_MODELS") != "1",
    reason="set DEUTSCH_TEST_REAL_MODELS=1 on Windows for the slow model check",
)


SAPI_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$speaker = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $voice = $speaker.GetInstalledVoices() | Where-Object {
        $_.VoiceInfo.Culture.Name -eq $env:DEUTSCH_TEST_CULTURE
    } | Select-Object -First 1
    if ($null -eq $voice) { Write-Output 'VOICE_MISSING'; exit 0 }
    $speaker.SelectVoice($voice.VoiceInfo.Name)
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
        16000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono
    )
    $speaker.SetOutputToWaveFile($env:DEUTSCH_TEST_WAV, $format)
    $speaker.Speak($env:DEUTSCH_TEST_TEXT)
} finally {
    $speaker.Dispose()
}
"""


@pytest.fixture(scope="module")
def local_engine():
    engine = LocalEngine()
    engine.prepare()
    return engine


def synthesize_wav(path: Path, culture: str, text: str) -> None:
    environment = dict(os.environ, DEUTSCH_TEST_CULTURE=culture,
                       DEUTSCH_TEST_TEXT=text, DEUTSCH_TEST_WAV=str(path))
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", SAPI_SCRIPT],
        check=True, capture_output=True, text=True, timeout=30, env=environment,
    )
    if "VOICE_MISSING" in result.stdout:
        pytest.skip(f"No installed {culture} SAPI voice")


@pytest.mark.parametrize("language,culture,text", [
    ("de", "de-DE", "Guten Morgen, ich lerne heute Deutsch."),
    ("en", "en-US", "Hello, I am learning German today."),
    ("zh", "zh-CN", "你好，我今天正在学习德语。"),
])
def test_real_sapi_speech_produces_german_caption(local_engine, tmp_path: Path, language, culture, text):
    wav_path = tmp_path / "voice.wav"
    synthesize_wav(wav_path, culture, text)
    with wave.open(str(wav_path), "rb") as audio_file:
        assert (audio_file.getframerate(), audio_file.getnchannels(), audio_file.getsampwidth()) == (16000, 1, 2)
        samples = np.frombuffer(audio_file.readframes(audio_file.getnframes()), dtype="<i2")
    caption = local_engine.process(samples.astype(np.float32) / 32768, 16000, 1, language, language)
    assert caption is not None
    assert caption.original.strip()
    assert caption.language == language
    if language == "de":
        assert caption.german is None
    else:
        assert caption.german and caption.german.strip()
    view = CaptionReducer(1, compare_original=True).apply(caption)
    assert view.primary
    assert view.secondary == (caption.original if language != "de" else None)


def test_quiet_sapi_speech_still_reaches_local_caption(local_engine, tmp_path: Path):
    wav_path = tmp_path / "quiet.wav"
    synthesize_wav(wav_path, "de-DE", "Guten Morgen. Ich lerne heute Deutsch.")
    with wave.open(str(wav_path), "rb") as audio_file:
        speech = np.frombuffer(audio_file.readframes(audio_file.getnframes()), dtype="<i2")
    quiet = speech.astype(np.float32) / 32768 * 0.003
    assert float(np.max(np.abs(quiet))) < 0.003
    detector = SileroSpeechDetector()
    segmenter = SpeechSegmenter(
        sample_rate=16000, frame_samples=1600, silence_seconds=0.3,
        voice_detector=detector,
    )
    padded = np.pad(quiet, (0, (-len(quiet)) % 1600 + 8000))
    clips = [clip for index in range(0, len(padded), 1600)
             for clip in segmenter.push(padded[index:index + 1600])]
    final = segmenter.flush()
    if final is not None:
        clips.append(final)
    assert clips, "Quiet but intelligible speech should not be discarded before ASR"
    captions = [local_engine.process(clip, 16000, 1, str(index), "de") for index, clip in enumerate(clips)]
    assert any(caption and "Deutsch" in caption.original for caption in captions)


@pytest.mark.skipif(
    os.environ.get("DEUTSCH_TEST_LIVE_LOOPBACK") != "1",
    reason="set DEUTSCH_TEST_LIVE_LOOPBACK=1 to audibly test the default playback device",
)
def test_real_windows_playback_loopback_reaches_local_caption(local_engine, tmp_path: Path):
    import winsound

    wav_path = tmp_path / "loopback.wav"
    synthesize_wav(wav_path, "de-DE", "Guten Morgen. Ich lerne Deutsch.")
    stop = Event()

    def play() -> None:
        time.sleep(0.5)
        winsound.PlaySound(str(wav_path), winsound.SND_FILENAME)
        time.sleep(0.5)
        stop.set()

    playback = Thread(target=play, daemon=True)
    playback.start()
    frames = []
    try:
        for frame in LoopbackSource().frames(stop):
            frames.append(frame)
            if len(frames) >= 100:
                stop.set()
                break
    finally:
        stop.set()
        playback.join(timeout=5)
    assert frames
    assert max(float(np.sqrt(np.mean(frame * frame))) for frame in frames) > 0.003
    detector = SileroSpeechDetector()
    segmenter = SpeechSegmenter(sample_rate=16000, frame_samples=1600,
                                silence_seconds=0.3, max_seconds=5.0, voice_detector=detector)
    clips = [clip for frame in frames for clip in segmenter.push(frame)]
    last = segmenter.flush()
    if last is not None:
        clips.append(last)
    assert clips
    caption = local_engine.process(clips[0], 16000, 1, "loopback", "de")
    assert caption is not None and caption.original


def test_long_german_speech_shows_local_preview_before_sentence_ends(local_engine, tmp_path: Path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from deutsch_overlay.config import Settings
    from deutsch_overlay.controller import CaptionController
    from deutsch_overlay.overlay import CaptionOverlay

    wav_path = tmp_path / "long_german.wav"
    synthesize_wav(
        wav_path, "de-DE",
        "Heute lernen wir gemeinsam die deutsche Sprache und sprechen langsam über viele interessante Wörter",
    )
    with wave.open(str(wav_path), "rb") as audio_file:
        samples = np.frombuffer(audio_file.readframes(audio_file.getnframes()), dtype="<i2")
    audio = samples.astype(np.float32) / 32768
    padded = np.pad(audio, (0, (-len(audio)) % 1600))
    frames = padded.reshape(-1, 1600)
    assert len(frames) >= 30

    playback_finished = []

    class RecordedSpeechSource:
        def frames(self, stop):
            for frame in frames:
                if stop.is_set():
                    break
                time.sleep(0.1)
                yield frame
            playback_finished.append(time.monotonic())

    app = QApplication.instance() or QApplication([])
    controller = CaptionController(
        source_factory=lambda _device: RecordedSpeechSource(),
        local_factory=lambda: local_engine,
    )
    overlay = CaptionOverlay(Settings(fade_seconds=8))
    captions = []
    statuses = []
    controller.view_changed.connect(lambda view: captions.append((time.monotonic(), view)))
    controller.view_changed.connect(overlay.show_caption)
    controller.status_changed.connect(statuses.append)
    try:
        assert controller.start(Settings(language_lock="de"))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and (not playback_finished or controller.running):
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()
        assert playback_finished, (statuses, captions)
        assert any(not view.final and at < playback_finished[0] for at, view in captions), captions
        assert any(view.final and "deutsche" in view.primary.lower() for _, view in captions), captions
        final_text = " ".join(view.primary for _, view in captions if view.final)
        assert "interessante Wörter" in final_text, final_text
        assert overlay.isVisible()
        assert overlay.primary_label.text() == captions[-1][1].primary
    finally:
        controller.stop()
        overlay.close()
