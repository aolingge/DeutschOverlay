"""Global Windows hotkeys without keyboard hooks or admin privileges."""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal


ACTIONS = {1: "compare", 2: "visibility", 3: "language", 4: "pause"}


def action_for_hotkey_id(hotkey_id: int) -> str | None:
    return ACTIONS.get(hotkey_id)


class HotkeyService(QObject):
    action = Signal(str)
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if sys.platform != "win32":
            self.failed.emit("Global hotkeys are available only on Windows")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        registered: list[int] = []
        mod_ctrl_alt_norepeat = 0x0002 | 0x0001 | 0x4000
        try:
            for hotkey_id in ACTIONS:
                if user32.RegisterHotKey(None, hotkey_id, mod_ctrl_alt_norepeat, ord(str(hotkey_id))):
                    registered.append(hotkey_id)
                else:
                    self.failed.emit(f"Ctrl+Alt+{hotkey_id} is already in use")
            msg = wintypes.MSG()
            while not self._stop.is_set():
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0x0001):
                    if msg.message == 0x0312:
                        action = action_for_hotkey_id(int(msg.wParam))
                        if action:
                            self.action.emit(action)
                time.sleep(0.03)
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)
