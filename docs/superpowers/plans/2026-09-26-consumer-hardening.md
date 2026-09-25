# Deutsch Overlay Consumer Hardening Implementation Plan

> **For agentic workers:** Implement each task against its focused tests, then run the complete suite and verify the packaged EXE. This plan extends the existing German captions design.

**Goal:** Make normal Windows playback captions recoverable and understandable for a nontechnical user, with a verified local EXE.

**Architecture:** Keep the existing PySide6, SoundCard/WASAPI and local model pipeline. The audio source retries only device-related failures and reports its state to the controller; settings preserve the user's selected device even when temporarily absent. Distribution checks run against the actual bundled folder.

**Tech Stack:** Python 3.12, PySide6, SoundCard, faster-whisper, CTranslate2, Azure Speech SDK, pytest, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-09-24-german-live-captions-design.md`

## Global Constraints

- Default to local processing; never send audio online without explicit in-app activation and valid credentials.
- Capture the selected playback endpoint's shared-mode mix. Do not silently switch a manually selected device or change Windows playback settings.
- Keep capture, recognition, and network waits off the Qt UI thread.
- Preserve unrelated settings and existing subtitle style options; do not persist captured audio or transcripts.
- No paid cloud calls, credential use, account actions, push, or public release in automated verification.

## Review Focus

1. Device disappearance, long unplugging, and reappearance must not strand local subtitles; stopping must still terminate promptly.
2. A missing manually selected device must remain visibly selected and must never be mislabeled as the system default.
3. A capture restart must reset speech boundaries, and stale results must never override captions from the resumed stream.
4. A missing system tray must leave a visible path to settings and exit.
5. The distribution check must use the bundled EXE and model folder; a successful unit suite alone does not prove Windows playback or overlay behavior.

---

### Task 1: Recoverable playback capture

**Files:** `src/deutsch_overlay/audio.py`, `src/deutsch_overlay/controller.py`, `tests/test_audio.py`, `tests/test_controller.py`.

**Interfaces:** `LoopbackSource.frames(stop_event)` retains mono 16 kHz frames. Add an opt-in persistent recovery mode for the application and a status callback for device waits/reconnections.

- [x] Write tests for missing default and manually selected devices, failure beyond the former five-second window, later recovery, bad format, and stop responsiveness; run to see failure.
- [x] Implement bounded backoff without choosing another output endpoint; reset capture generation on every reopened recorder.
- [x] Show a user-facing wait/recovered status through the existing controller status signal; run focused and full tests.

### Task 2: Honest settings and window access

**Files:** `src/deutsch_overlay/settings_window.py`, `src/deutsch_overlay/app.py`, `tests/test_app_smoke.py`.

**Interfaces:** Device combo distinguishes system default from a saved unavailable endpoint. Tray availability controls whether closing the settings window is allowed to hide it.

- [x] Write failing Qt tests for a saved missing device, refresh without losing pending selection, and missing tray.
- [x] Implement visible missing-device choice and an accessible fallback window/exit path.
- [x] Verify actual settings save/restore and the caption preview on the Windows Qt platform.

### Task 3: Release and online lifecycle checks

**Files:** `src/deutsch_overlay/engines/azure.py`, `tests/test_azure_engine.py`, `scripts/build-exe.ps1`, `README.md`, `docs/verification.md`.

- [x] Verify the Azure SDK future API against official docs; reproduce an unresponsive fake future and prevent an indefinite application lifecycle wait without sending paid audio.
- [x] Check model files and runtime assets in the produced folder, add version information and model attribution.
- [x] Run dependency audit, focused tests, full suite, real-model tests, final EXE self-tests, and shortcut check.

### Task 4: Real playback and visible captions

**Files:** `tests/test_video_playback.py`, `docs/verification.md`.

- [x] Record current default/output device and simultaneous FFplay test-tone loopback evidence; current default xiaodu peak 0.0083, other endpoints zero.
- [x] Play synthetic German, English and Chinese MP4 clips through Windows; current 3/3 pass, model inference 0.22–0.27 seconds per clip.
- [x] Verify the real Windows overlay displays a German-only and a bilingual caption and record any limitations of full-screen games or protected/exclusive audio.
- [x] Document what was actually tested, including source audio quality and total latency limits.
