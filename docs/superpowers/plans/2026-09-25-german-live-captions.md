# German Live Captions Implementation Plan

> **For agentic workers:** Steps use checkboxes for tracking. Read the linked spec before changing code.

**Goal:** Ship a Windows EXE that overlays German captions for German game speech and German translations for English or Chinese computer audio.

**Architecture:** A PySide6 process owns the tray, settings, and transparent overlay. A worker captures the selected WASAPI output device and sends short speech segments to a swappable local or Azure engine. Engines return typed caption events to a thread-safe controller; the Qt thread renders only immutable view state.

**Tech Stack:** Python 3.12, PySide6, soundcard/WASAPI, numpy, faster-whisper, CTranslate2 plus Helsinki-NLP OPUS-MT, Azure Speech SDK, pytest, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-09-24-german-live-captions-design.md`

## Global Constraints

- Windows 11 x64 is the delivery target; the user machine has an RTX 5060 Laptop GPU with 8 GiB VRAM.
- Default audio is the selected output endpoint; microphone and per-process capture are outside this version.
- German source is transcribed; English and Chinese source is translated to German; one-key original comparison is required.
- Local is default. Cloud audio is transmitted only after explicit in-app enablement and credential configuration.
- Do not persist audio, transcripts, or credentials in project files or logs.
- Deliver a folder containing an EXE and separately managed local models; do not embed multi-GiB models in one EXE.
- Run the focused test before each implementation and the full suite before each feature commit. Do not push or publish.

## Review Focus

1. A silent or absent output device must leave the UI responsive and show a recoverable status; Task 3 tests silence and Task 7 tests source failure.
2. A stale transcription from a prior engine session must never overwrite the current caption; Task 2 and Task 7 test generation IDs.
3. German captions must not be translated or duplicated in comparison mode; Task 2 tests this routing.
4. Missing cloud credentials and cloud limits must not send audio or silently retry; Task 6 tests both.
5. Bundled EXE must find its resources and allow a missing-model recovery path; Task 8 checks a clean launch.

## File Map

- `src/deutsch_overlay/config.py`: validated user settings and AppData persistence.
- `src/deutsch_overlay/captions.py`: immutable caption events, language routing, subtitle state.
- `src/deutsch_overlay/audio.py`: WASAPI loopback adapter and pure segmenter.
- `src/deutsch_overlay/engines/base.py`: engine contract.
- `src/deutsch_overlay/engines/local.py`: faster-whisper transcription and local translation.
- `src/deutsch_overlay/engines/azure.py`: opt-in cloud speech translation and usage limit.
- `src/deutsch_overlay/overlay.py`: transparent Qt caption window.
- `src/deutsch_overlay/settings_window.py`: user controls and status.
- `src/deutsch_overlay/hotkeys.py`: Windows global hotkeys.
- `src/deutsch_overlay/controller.py`: session generation, worker lifecycle, engine selection.
- `src/deutsch_overlay/app.py`: application entry point and tray.
- `tests/`: pure units, adapter fakes, Qt and integration checks.
- `scripts/`: model preparation and Windows build commands.

### Task 1: Project foundation and settings

**Files:** Create `pyproject.toml`, `.gitignore`, `src/deutsch_overlay/__init__.py`, `src/deutsch_overlay/config.py`, `tests/test_config.py`.

**Interfaces:** `Settings` is a frozen dataclass. `load_settings(path: Path) -> Settings` returns defaults for a missing file. `save_settings(path: Path, settings: Settings) -> None` writes atomically. Values include source device ID, mode, language lock, original comparison, overlay geometry/style, fade seconds, and online minute cap.

- [ ] Write `tests/test_config.py` first: missing file yields local/auto/German-only defaults; invalid JSON or out-of-range opacity/fade/minute values gives a clear error; save/load round-trip preserves values.
- [ ] Run `python -m pytest tests/test_config.py -q` and observe failure because `deutsch_overlay.config` does not exist.
- [ ] Create the package and implement immutable validated settings and atomic JSON persistence. Keep credentials out of the settings schema.
- [ ] Run focused tests and then `python -m pytest -q`; commit `feat: add desktop app settings foundation`.

### Task 2: Caption routing and stale-result guard

**Files:** Create `src/deutsch_overlay/captions.py`, `tests/test_captions.py`.

**Interfaces:** `CaptionEvent(session_id: int, segment_id: str, language: str, original: str, german: str | None, final: bool, timestamp: float)`; `CaptionReducer.apply(event) -> CaptionView | None`. `CaptionView` contains German primary text, optional original secondary text, and final state. `set_compare_original(bool)` changes rendering without re-running translation.

- [ ] Write failing tests for German direct display, English/Chinese German routing, comparison toggle, duplicate segment updates, two-line limit, and rejection of an old session ID.
- [ ] Run `python -m pytest tests/test_captions.py -q` to see import failures.
- [ ] Implement the reducer as small pure functions and frozen data structures; do not mutate event objects.
- [ ] Run focused and full tests; commit `feat: route multilingual caption events`.

### Task 3: Audio capture and speech segmentation

**Files:** Create `src/deutsch_overlay/audio.py`, `tests/test_audio.py`.

**Interfaces:** `list_output_devices() -> list[OutputDevice]`; `LoopbackSource(device_id: str | None).frames(stop_event) -> Iterator[np.ndarray]` yields mono 16 kHz float32 frames. `SpeechSegmenter.push(frame: np.ndarray) -> list[np.ndarray]` emits bounded speech clips and `flush()` emits any pending clip.

- [ ] Write failing tests using synthetic silence, speech-like frames, a disconnected recorder fake, max-length clipping, and device ID resolution.
- [ ] Run `python -m pytest tests/test_audio.py -q` and observe failure.
- [ ] Implement the pure segmenter before the soundcard adapter. Use a bounded queue and explicit device errors; never block the Qt thread.
- [ ] Verify focused tests plus a manual list of output devices. Commit `feat: capture Windows output audio`.

### Task 4: Local recognition and translation

**Files:** Create `src/deutsch_overlay/engines/base.py`, `src/deutsch_overlay/engines/local.py`, `src/deutsch_overlay/models.py`, `tests/test_local_engine.py`, `scripts/prepare-models.ps1`.

**Interfaces:** `CaptionEngine.process(audio: np.ndarray, sample_rate: int, session_id: int, segment_id: str, language_lock: str | None) -> CaptionEvent`. `LocalEngine` lazily loads faster-whisper and the two OPUS-MT translators; it returns original German directly. `ModelStore` checks model files and returns specific missing-model errors.

- [ ] Write failing tests with fake ASR and translators for each language, language lock, unknown speech, missing model, and CPU fallback warning.
- [ ] Run `python -m pytest tests/test_local_engine.py -q` to see the failures.
- [ ] Implement lazy model loading, safe model-path resolution, German passthrough, English/Chinese translation, and model preparation with checksums/attribution where provided upstream.
- [ ] Run tests and a measured local German sample transcription; commit `feat: add offline multilingual captions`.

### Task 5: Overlay, settings, tray, and hotkeys

**Files:** Create `src/deutsch_overlay/overlay.py`, `src/deutsch_overlay/settings_window.py`, `src/deutsch_overlay/hotkeys.py`, `tests/test_overlay.py`.

**Interfaces:** `CaptionOverlay.show_caption(CaptionView)`, `hide_caption()`, `set_locked(bool)`, `set_style(Settings)`; `SettingsWindow` emits immutable settings changes. Global hotkeys emit Qt signals for compare, visibility, language cycle, and pause.

- [ ] Write failing offscreen Qt tests for German-only and comparison layout, timed fade, position persistence, and lock state; test the hotkey action mapping separately from Windows registration.
- [ ] Run `python -m pytest tests/test_overlay.py -q` and observe failure.
- [ ] Build the frameless topmost click-through overlay, configuration UI, tray actions, and Win32 RegisterHotKey bridge. Make font/width/opacity configurable and errors visible in settings.
- [ ] Run offscreen tests and a real overlay test above a borderless game or window; commit `feat: add fixed game caption overlay`.

### Task 6: Opt-in Azure online engine

**Files:** Create `src/deutsch_overlay/engines/azure.py`, `src/deutsch_overlay/credentials.py`, `tests/test_azure_engine.py`.

**Interfaces:** `AzureEngine` accepts PCM segments or stream frames, source candidates de-DE/en-US/zh-CN, German target, a credential provider, and an `OnlineBudget` with `allow(seconds) -> bool`. It emits the same `CaptionEvent` contract as local mode.

- [ ] Write failing tests with a fake SDK/credential store for no-key refusal, German source passthrough, translation, network error, app usage cap, and no retry after failure.
- [ ] Run `python -m pytest tests/test_azure_engine.py -q` to see failure.
- [ ] Implement the SDK adapter with explicit enablement and Windows protected credential storage. Redact all diagnostic output and surface usage seconds in UI state.
- [ ] Run focused tests; use live Azure only after the user configures their own account and approves real credential use. Commit `feat: add opt-in online caption engine`.

### Task 7: End-to-end application lifecycle

**Files:** Create `src/deutsch_overlay/controller.py`, `src/deutsch_overlay/app.py`, `tests/test_controller.py`, `tests/test_app_smoke.py`.

**Interfaces:** `CaptionController.start(settings)`, `switch_mode(mode)`, `pause()`, `stop()` manage one session generation. Qt receives immutable status/caption signals from worker threads. `python -m deutsch_overlay.app` is the dev entry point.

- [ ] Write failing tests for start/stop, switching modes, dropped stale results, source disconnect, and no-key online selection with fakes.
- [ ] Run `python -m pytest tests/test_controller.py tests/test_app_smoke.py -q` and see failure.
- [ ] Wire audio, engine, caption reducer, overlay, settings and tray; keep all blocking capture/inference off the UI thread.
- [ ] Run full suite and an audio-to-overlay smoke session; commit `feat: wire live caption desktop app`.

### Task 8: EXE, docs, and measured acceptance

**Files:** Create `scripts/build-exe.ps1`, `README.md`, `docs/verification.md`, `tests/test_package_layout.py`.

**Interfaces:** `scripts/build-exe.ps1` produces `dist/DeutschOverlay/DeutschOverlay.exe`; local model files are separate and checked on startup. `README.md` explains start, models, online opt-in, and shortcuts.

- [ ] Write a failing layout test for the expected launch file and model recovery hint, then make package paths stable under PyInstaller.
- [ ] Run `python -m pytest tests/test_package_layout.py -q`; build with PyInstaller on Windows.
- [ ] Launch the EXE outside the terminal. Verify German game overlay for at least 15 minutes and record German/English/Chinese sample results and latency in `docs/verification.md`; mark unavailable live tests clearly.
- [ ] Run `python -m pytest --cov=deutsch_overlay --cov-report=term-missing -q`, inspect secrets and diff, then commit `build: package German live captions for Windows`.

## Execution Notes

The user has already instructed this session to initialize Git and start building. Execute natively in this session, keeping a progress record and requesting user input only where actual account credentials, paid cloud usage, or a specific game session is required. Before any push or publication, stop for explicit authorization.
