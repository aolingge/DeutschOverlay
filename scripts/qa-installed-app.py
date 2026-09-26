"""Opt-in black-box Windows check of a frozen app and its real caption window."""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from deutsch_overlay.config import Settings, save_settings


USER32 = ctypes.windll.user32
ENUM_CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def windows_for_pid(pid: int) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    @ENUM_CALLBACK
    def inspect(hwnd: int, _unused: int) -> bool:
        owner = ctypes.c_ulong()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and USER32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(256)
            USER32.GetWindowTextW(hwnd, title, len(title))
            found.append((hwnd, title.value))
        return True

    USER32.EnumWindows(inspect, 0)
    return found


def wait_for_window(pid: int, *, titled: bool, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = [(handle, title) for handle, title in windows_for_pid(pid)
                   if ("设置" in title) is titled]
        if matches:
            return matches[0][0]
        time.sleep(0.1)
    raise RuntimeError(
        f"No {'settings' if titled else 'caption'} window became visible; "
        f"current windows={windows_for_pid(pid)}"
    )


def inspect_accessible_window(hwnd: int, *, invoke: str | None = None) -> str:
    script = r"""
Add-Type -AssemblyName UIAutomationClient
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr][int64]$env:DEUTSCH_QA_HWND)
if ($env:DEUTSCH_QA_INVOKE) {
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty, $env:DEUTSCH_QA_INVOKE)
    $button = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
    if ($null -eq $button) { throw 'Requested button not found' }
    $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
}
$nodes = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition)
for ($i = 0; $i -lt $nodes.Count; $i++) {
    $node = $nodes.Item($i)
    if ($node.Current.Name) { Write-Output $node.Current.Name }
}
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        env={**os.environ, "DEUTSCH_QA_HWND": str(hwnd), "DEUTSCH_QA_INVOKE": invoke or ""},
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "UI Automation failed")
    return result.stdout


def synthesize_video(directory: Path) -> Path:
    wav, video = directory / "speech.wav", directory / "speech.mp4"
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s=[System.Speech.Synthesis.SpeechSynthesizer]::new(); "
        "try { "
        "$v=$s.GetInstalledVoices() | Where-Object {$_.VoiceInfo.Culture.Name -eq 'de-DE'} "
        "| Select-Object -First 1; "
        "if ($null -eq $v) { throw 'de-DE voice missing' }; "
        "$s.SelectVoice($v.VoiceInfo.Name); "
        "$s.SetOutputToWaveFile($env:DEUTSCH_QA_WAV); "
        "$s.Speak('Guten Morgen. Ich lerne heute Deutsch.'); "
        "} finally { $s.Dispose() }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True,
                   env={**os.environ, "DEUTSCH_QA_WAV": str(wav)}, timeout=30)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=320x180:r=25", "-i", str(wav), "-shortest",
        "-c:v", "mpeg4", "-c:a", "aac", str(video),
    ], check=True, timeout=30)
    return video


def wait_until_listening(settings_hwnd: int, timeout: float = 40) -> None:
    deadline = time.monotonic() + timeout
    latest = ""
    while time.monotonic() < deadline:
        latest = inspect_accessible_window(settings_hwnd)
        if "正在监听" in latest:
            return
        time.sleep(0.25)
    raise RuntimeError(f"Frozen app did not start listening: {latest.splitlines()[-5:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path, help="Full path to installed DeutschOverlay.exe")
    parser.add_argument("--output", type=Path, required=True, help="QA report directory")
    args = parser.parse_args()
    if not args.exe.is_file():
        raise FileNotFoundError(args.exe)
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deutsch-overlay-qa-") as scratch:
        data_root = Path(scratch)
        save_settings(data_root / "DeutschOverlay" / "settings.json", Settings(
            language_lock="de", compare_original=True, fade_seconds=20,
        ))
        video = synthesize_video(data_root)
        process = subprocess.Popen([str(args.exe)], env={
            **os.environ, "LOCALAPPDATA": str(data_root), "QT_QPA_PLATFORM": "windows",
        })
        player = None
        try:
            settings_hwnd = wait_for_window(process.pid, titled=True, timeout=20)
            print(f"settings_window={settings_hwnd} visible=True")
            inspect_accessible_window(settings_hwnd, invoke="应用并预览")
            print(f"after_preview_settings={inspect_accessible_window(settings_hwnd).splitlines()[-4:]}")
            preview_hwnd = wait_for_window(process.pid, titled=False, timeout=5)
            preview_text = inspect_accessible_window(preview_hwnd)
            if "Guten Tag" not in preview_text:
                raise RuntimeError(f"Preview window did not expose German text: {preview_text}")
            print("preview_caption=visible and accessible")
            wait_until_listening(settings_hwnd)
            play_command = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-i", str(video)]
            player = subprocess.Popen(play_command, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.PIPE, text=True)
            completed_plays = 0
            deadline = time.monotonic() + 25
            input_states: list[str] = []
            while time.monotonic() < deadline:
                for hwnd, title in windows_for_pid(process.pid):
                    if "设置" in title:
                        continue
                    text = inspect_accessible_window(hwnd)
                    if "Deutsch" in text and "Guten Tag!" not in text:
                        (args.output / "live-caption.txt").write_text(text, encoding="utf-8")
                        print(f"live_caption=visible text={text.strip()!r}")
                        return 0
                status = inspect_accessible_window(settings_hwnd)
                input_states.extend(line for line in status.splitlines()
                                    if "播放声" in line or "正在监听" in line)
                if player.poll() is not None:
                    error = player.stderr.read() if player.stderr else ""
                    if player.returncode != 0:
                        raise RuntimeError(f"Player failed: {player.returncode}; {error}")
                    completed_plays += 1
                    if completed_plays >= 5:
                        break
                    player = subprocess.Popen(play_command, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.PIPE, text=True)
                time.sleep(0.5)
            raise RuntimeError(f"No live caption; recent input state: {input_states[-5:]}")
            return 0
        finally:
            if player is not None and player.poll() is None:
                player.terminate()
                player.wait(timeout=5)
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
