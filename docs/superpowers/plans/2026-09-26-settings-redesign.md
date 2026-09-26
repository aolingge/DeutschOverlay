# Settings Redesign Implementation Plan

**Goal:** Make the Windows caption app simple to operate and visually polished while retaining every existing control.

**Architecture:** Rebuild the PySide6 settings view as three navigable pages with common save and preview actions. Add controls to the caption overlay only while it is unlocked, and connect them through DesktopApp's existing actions.

**Tech Stack:** Python 3.12, PySide6, pytest, PyInstaller, Windows UI Automation.

**Spec:** `docs/superpowers/specs/2026-09-26-settings-redesign.md`

## Tasks

1. Add tests for visible primary controls, navigation, choice persistence, and overlay control visibility/signals; observe failure.
2. Add a small reusable choice widget and scoped Qt theme; rebuild settings layout into Subtitle, Appearance, and Online pages while retaining settings serialization and signal interfaces. Run focused tests.
3. Add unlocked-only overlay controls and connect app signals. Check locked click-through, layout, preview, and drag behavior with focused tests.
4. Capture and inspect actual Windows Qt screenshots, fix visual/interaction defects, then run full suite and coverage.
5. Update README, build complete EXE, install locally, verify the desktop shortcut and installed app with UI Automation and actual audio playback.

## Review focus

- Default settings page must expose German-only/bilingual and sound language choices without scrolling.
- All existing appearance values, Azure settings, and the saved custom position must survive page switches and save.
- Overlay buttons must disappear when locked and remain clickable when unlocked; locked overlay must pass through mouse input.
- Settings changes must not start online processing without a deliberate mode selection.
- A user must be able to find the preview and current status at every page.
