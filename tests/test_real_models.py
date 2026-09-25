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
