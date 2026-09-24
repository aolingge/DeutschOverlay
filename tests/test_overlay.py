import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QGuiApplication

from deutsch_overlay.captions import CaptionView
from deutsch_overlay.config import Settings
from deutsch_overlay.hotkeys import action_for_hotkey_id
from deutsch_overlay.overlay import CaptionOverlay


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def view(secondary=None):
    return CaptionView(1, "a", "Hallo Welt", secondary, True, 1.0)


def test_overlay_shows_german_and_optional_original(qapp):
    overlay = CaptionOverlay(Settings(fade_seconds=5))
    overlay.show_caption(view())
    assert overlay.primary_label.text() == "Hallo Welt"
    assert overlay.secondary_label.isHidden()
    overlay.show_caption(view("Hello world"))
    assert overlay.secondary_label.text() == "Hello world"
    assert not overlay.secondary_label.isHidden()
    overlay.close()


def test_lock_mode_passes_mouse_input_through(qapp):
    overlay = CaptionOverlay(Settings())
    overlay.set_locked(True)
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    overlay.set_locked(False)
    assert not overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    overlay.close()


def test_overlay_position_and_style_are_applied(qapp):
    settings = Settings(overlay_x=31, overlay_y=53, overlay_width=730, font_size=23)
    overlay = CaptionOverlay(settings)
    assert (overlay.x(), overlay.y(), overlay.width()) == (31, 53, 730)
    assert overlay.primary_label.font().pointSize() == 23
    overlay.close()


def test_final_caption_hides_after_delay(qapp):
    from PySide6.QtTest import QTest

    overlay = CaptionOverlay(Settings(fade_seconds=0.02))
    overlay.show_caption(view())
    assert overlay.isVisible()
    QTest.qWait(70)
    assert not overlay.isVisible()
    overlay.close()


def test_long_caption_grows_to_fit_wrapped_text(qapp):
    overlay = CaptionOverlay(Settings(overlay_width=240, font_size=24, fade_seconds=5))
    overlay.show_caption(CaptionView(1, "long", "Das ist ein langer deutscher Satz. " * 12, None, True, 1.0))
    assert overlay.primary_label.height() >= overlay.primary_label.heightForWidth(204)
    assert overlay.height() >= overlay.primary_label.height() + 18
    overlay.close()


def test_saved_position_near_monitor_edge_is_clamped(qapp):
    rect = QGuiApplication.primaryScreen().availableGeometry()
    overlay = CaptionOverlay(Settings(overlay_x=rect.right() - 5, overlay_y=rect.top(), overlay_width=240))
    assert overlay.x() + overlay.width() <= rect.right() + 1
    overlay.close()


def test_global_hotkey_action_mapping():
    assert action_for_hotkey_id(1) == "compare"
    assert action_for_hotkey_id(2) == "visibility"
    assert action_for_hotkey_id(3) == "language"
    assert action_for_hotkey_id(4) == "pause"
    assert action_for_hotkey_id(999) is None
