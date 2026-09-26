"""Capture the real Qt settings pages without including desktop content."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from deutsch_overlay.config import Settings
from deutsch_overlay.settings_window import SettingsWindow


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/assets/ui-redesign"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication(sys.argv)
    window = SettingsWindow(Settings(), [])
    window.show()
    for page, button in (("subtitles", window.nav_subtitles),
                         ("appearance", window.nav_appearance),
                         ("online", window.nav_online)):
        button.click()
        application.processEvents()
        output = args.output / f"settings-{page}.png"
        if not window.grab().save(str(output)):
            raise RuntimeError(f"Could not save {output}")
        print(output.resolve())
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
