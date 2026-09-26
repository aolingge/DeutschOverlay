"""The main caption controls remain discoverable and the overlay stays unobtrusive."""

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from deutsch_overlay.config import Settings
from deutsch_overlay.captions import CaptionView
from deutsch_overlay.overlay import CaptionOverlay
from deutsch_overlay.settings_window import SettingsWindow


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_primary_choices_are_visible_on_first_page(qapp):
    window = SettingsWindow(Settings(), [])
    window.show()
    qapp.processEvents()
    assert window.current_page() == "subtitles"
    assert window.subtitle_combo.isVisible()
    assert window.language_combo.isVisible()
    assert window.device_combo.isVisible()
    assert window.preview_button.isVisible()
    assert window.settings_scroll.horizontalScrollBar().maximum() == 0
    window.close()


def test_initial_window_fits_available_screen(qapp):
    window = SettingsWindow(Settings(), [])
    available = qapp.primaryScreen().availableGeometry()
    window.show()
    qapp.processEvents()
    assert window.geometry().left() >= available.left()
    assert window.geometry().top() >= available.top()
    assert window.geometry().right() <= available.right()
    assert window.geometry().bottom() <= available.bottom()
    window.close()


def test_pages_keep_unsaved_choices_and_save_all_fields(qapp):
    window = SettingsWindow(Settings(), [])
    submitted = []
    window.settings_changed.connect(submitted.append)
    window.subtitle_combo.setCurrentIndex(window.subtitle_combo.findData(True))
    window.position_combo.setCurrentIndex(window.position_combo.findData("top-right"))
    window.nav_appearance.click()
    window.style_combo.setCurrentIndex(window.style_combo.findData("light"))
    window.nav_online.click()
    window.online_limit_spin.setValue(45)
    window.nav_subtitles.click()
    window._apply()
    assert window.current_page() == "subtitles"
    assert submitted[-1].compare_original is True
    assert submitted[-1].overlay_position == "top-right"
    assert submitted[-1].background_color == "#F5F5F5"
    assert submitted[-1].online_minutes_limit == 45
    window.close()


def test_external_quick_action_keeps_unapplied_edits(qapp):
    window = SettingsWindow(Settings(), [])
    window.mode_combo.setCurrentIndex(window.mode_combo.findData("online"))
    window.font_spin.setValue(35)
    window.update_settings(replace(Settings(), compare_original=True))
    assert window.mode_combo.currentData() == "online"
    assert window.font_spin.value() == 35
    assert window.subtitle_combo.currentData() is True
    assert window.has_pending_changes()
    window.close()


def test_overlay_controls_only_exist_during_position_adjustment(qapp):
    overlay = CaptionOverlay(Settings())
    try:
        calls = []
        overlay.compare_requested.connect(lambda: calls.append("compare"))
        overlay.settings_requested.connect(lambda: calls.append("settings"))
        overlay.lock_requested.connect(lambda: calls.append("lock"))
        assert not overlay.control_bar.isVisible()
        assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
        overlay.set_locked(False)
        overlay.show()
        qapp.processEvents()
        assert overlay.control_bar.isVisible()
        overlay.compare_button.click()
        overlay.settings_button.click()
        overlay.done_button.click()
        assert calls == ["compare", "settings", "lock"]
        overlay.set_locked(True)
        assert not overlay.control_bar.isVisible()
        assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    finally:
        overlay.close()


def test_narrow_overlay_controls_fit_inside_caption_width(qapp):
    overlay = CaptionOverlay(Settings(overlay_width=240))
    try:
        overlay.set_locked(False)
        overlay.show_caption(CaptionView(1, "narrow", "Hallo", None, True, 0.0), persistent=True)
        qapp.processEvents()
        assert overlay.control_bar.minimumSizeHint().width() <= overlay.width()
        assert overlay.done_button.geometry().right() <= overlay.control_bar.width()
        assert overlay.compare_button.text() == "双语"
    finally:
        overlay.close()
