"""Capture the real Windows caption window without recording the desktop."""

from __future__ import annotations

import os
import sys
import ctypes
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from deutsch_overlay.captions import CaptionView
from deutsch_overlay.config import Settings
from deutsch_overlay.overlay import CaptionOverlay


def main() -> int:
    app = QApplication.instance() or QApplication([])
    if app.platformName() != "windows":
        raise RuntimeError("This check needs the real Windows Qt platform")
    target = Path(__file__).resolve().parents[1] / "docs" / "assets"
    target.mkdir(parents=True, exist_ok=True)
    overlay = CaptionOverlay(Settings(fade_seconds=30, overlay_position="bottom-center"))
    try:
        for name, original in (("german", None), ("bilingual", "你好，我在学习德语。")):
            overlay.show_caption(CaptionView(
                1, name, "Guten Tag! Ich lerne Deutsch.", original, True, 0.0,
            ))
            app.processEvents()
            QTest.qWait(100)
            assert overlay.isVisible() and overlay.primary_label.isVisible()
            assert overlay.secondary_label.isVisible() is (original is not None)
            if not ctypes.windll.user32.IsWindowVisible(int(overlay.winId())):
                raise RuntimeError("Windows reports the caption window as hidden")
            # QWidget.grab excludes private desktop content behind transparency.
            image = overlay.grab()
            if image.isNull():
                raise RuntimeError("Windows could not capture the caption window")
            path = target / f"windows-overlay-{name}.png"
            if not image.save(str(path)):
                raise RuntimeError(f"Could not save {path}")
            print(f"{name}: visible={overlay.isVisible()} size={image.width()}x{image.height()} path={path}")
    finally:
        overlay.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
