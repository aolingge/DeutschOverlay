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


def test_provisional_caption_is_visually_distinct_until_final(qapp):
    overlay = CaptionOverlay(Settings())
    provisional = CaptionView(1, "same", "Guten", None, False, 1.0)
    final = CaptionView(1, "same", "Guten Tag", None, True, 2.0)
    overlay.show_caption(provisional)
    assert overlay.primary_label.graphicsEffect().opacity() < 1
    overlay.show_caption(final)
    assert overlay.primary_label.graphicsEffect().opacity() == 1
    assert overlay.primary_label.text() == "Guten Tag"
    overlay.close()


def test_lock_mode_passes_mouse_input_through(qapp):
    overlay = CaptionOverlay(Settings())
    overlay.set_locked(True)
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    overlay.set_locked(False)
    assert not overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    overlay.close()


def test_overlay_position_and_style_are_applied(qapp):
    settings = Settings(overlay_x=31, overlay_y=53, overlay_position="custom", overlay_width=730, font_size=23)
    overlay = CaptionOverlay(settings)
    assert (overlay.x(), overlay.y(), overlay.width()) == (31, 53, 730)
    assert overlay.primary_label.font().pointSize() == 23
    overlay.close()


def test_background_frame_style_and_padding_update_live(qapp):
    overlay = CaptionOverlay(Settings())
    overlay.show_caption(view("你好"))
    styled = Settings(
        background_color="#123456", primary_color="#ABCDEF", secondary_color="#FEDCBA",
        border_color="#FFCC00", border_width=2, corner_radius=14,
        padding_horizontal=24, padding_vertical=11, opacity=0.5,
    )
    overlay.set_style(styled)
    assert "rgba(18, 52, 86, 128)" in overlay.frame.styleSheet()
    assert "border: 2px solid #FFCC00" in overlay.frame.styleSheet()
    assert "border-radius: 14px" in overlay.frame.styleSheet()
    assert "#ABCDEF" in overlay.primary_label.styleSheet()
    assert "#FEDCBA" in overlay.secondary_label.styleSheet()
    margins = overlay.inner_layout.contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (26, 13, 26, 13)
    assert overlay.height() >= overlay.primary_label.height() + overlay.secondary_label.height() + 26
    overlay.close()


def test_transparent_background_does_not_hide_caption_text(qapp):
    overlay = CaptionOverlay(Settings(opacity=0))
    overlay.show_caption(view())
    assert "rgba(0, 0, 0, 0)" in overlay.frame.styleSheet()
    assert overlay.primary_label.isVisible()
    overlay.close()


def test_rendered_frame_pixel_changes_with_selected_background(qapp):
    overlay = CaptionOverlay(Settings(background_color="#123456", opacity=1, corner_radius=0))
    overlay.show_caption(view())
    qapp.processEvents()
    image = overlay.frame.grab().toImage()
    color = image.pixelColor(4, image.height() // 2)
    assert (color.red(), color.green(), color.blue()) == (18, 52, 86)
    overlay.close()


def test_border_remains_visible_with_transparent_background(qapp):
    overlay = CaptionOverlay(Settings(opacity=0, border_width=2, border_color="#FFCC00", corner_radius=0))
    overlay.show_caption(view())
    qapp.processEvents()
    image = overlay.frame.grab().toImage()
    border = image.pixelColor(1, image.height() // 2)
    background = image.pixelColor(6, image.height() // 2)
    assert (border.red(), border.green(), border.blue(), border.alpha()) == (255, 204, 0, 255)
    assert background.alpha() == 0
    overlay.close()


def test_outline_is_only_around_outer_frame_not_each_text_line(qapp):
    overlay = CaptionOverlay(Settings(background_color="#101820", border_color="#F2D88A",
                                      border_width=2, corner_radius=0, opacity=1))
    overlay.show_caption(view("你好"))
    qapp.processEvents()
    image = overlay.frame.grab().toImage()
    outer = image.pixelColor(1, image.height() // 2)
    inner = image.pixelColor(overlay.primary_label.x() + 3, overlay.primary_label.y() + 1)
    assert (outer.red(), outer.green(), outer.blue()) == (242, 216, 138)
    assert (inner.red(), inner.green(), inner.blue()) == (16, 24, 32)
    assert overlay.secondary_label.y() >= (
        overlay.primary_label.y() + overlay.primary_label.height() + overlay.inner_layout.spacing()
    )
    overlay.close()


def test_caption_text_is_always_treated_as_plain_text(qapp):
    overlay = CaptionOverlay(Settings())
    overlay.show_caption(CaptionView(1, "markup", "<b>Hallo</b>", "<img src='local'>", True, 1.0))
    assert overlay.primary_label.textFormat() == Qt.TextFormat.PlainText
    assert overlay.secondary_label.textFormat() == Qt.TextFormat.PlainText
    assert overlay.primary_label.text() == "<b>Hallo</b>"
    overlay.close()


def test_overlay_width_is_limited_to_current_screen(qapp):
    rect = QGuiApplication.primaryScreen().availableGeometry()
    overlay = CaptionOverlay(Settings(overlay_width=3840, overlay_position="top-center"))
    overlay.show_caption(view())
    assert overlay.width() <= rect.width()
    assert rect.left() <= overlay.x()
    assert overlay.x() + overlay.width() <= rect.right() + 1
    overlay.close()


def test_extreme_style_and_long_bilingual_caption_stay_on_screen(qapp):
    rect = QGuiApplication.primaryScreen().availableGeometry()
    settings = Settings(overlay_width=240, font_size=72, padding_horizontal=40,
                        padding_vertical=24, border_width=8, overlay_position="bottom-center")
    overlay = CaptionOverlay(settings)
    overlay.show_caption(CaptionView(1, "long", "Sehr langer deutscher Untertitel. " * 8,
                                     "这是一段很长的中文原文。" * 8, True, 1.0))
    assert overlay.height() <= max(120, round(rect.height() * 0.4))
    assert rect.top() <= overlay.y()
    assert overlay.y() + overlay.height() <= rect.bottom() + 1
    assert overlay.primary_label.text()
    qapp.processEvents()
    assert overlay.secondary_label.y() >= (
        overlay.primary_label.y() + overlay.primary_label.height() + overlay.inner_layout.spacing()
    )
    assert overlay.primary_label.height() >= overlay.primary_label.heightForWidth(overlay.primary_label.width())
    assert overlay.secondary_label.height() >= overlay.secondary_label.heightForWidth(overlay.secondary_label.width())
    overlay.show_caption(view("你好"))
    assert overlay.primary_label.text() == "Hallo Welt"
    overlay.close()


def test_short_original_does_not_waste_german_caption_height(qapp):
    settings = Settings(overlay_width=240, font_size=12, padding_horizontal=40,
                        padding_vertical=24, border_width=8)
    overlay = CaptionOverlay(settings)
    overlay.show_caption(CaptionView(1, "long", "Ein sehr langer deutscher Satz. " * 15,
                                     "中", True, 1.0))
    assert len(overlay.primary_label.text()) > 80
    assert overlay.secondary_label.text() == "中"
    assert overlay.height() > 280
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
    overlay = CaptionOverlay(Settings(overlay_x=rect.right() - 5, overlay_y=rect.top(), overlay_position="custom", overlay_width=240))
    assert overlay.x() + overlay.width() <= rect.right() + 1
    overlay.close()


def test_position_preset_overrides_stale_saved_coordinates(qapp):
    rect = QGuiApplication.primaryScreen().availableGeometry()
    overlay = CaptionOverlay(Settings(overlay_position="top-left", overlay_x=500, overlay_y=500, overlay_width=240))
    assert (overlay.x(), overlay.y()) == (rect.left() + 45, rect.top() + 55)
    overlay.close()


def test_interim_caption_expires_when_no_final_result_arrives(qapp):
    from PySide6.QtTest import QTest

    overlay = CaptionOverlay(Settings(fade_seconds=0))
    overlay.show_caption(CaptionView(1, "interim", "Hallo", None, False, 1.0))
    assert overlay.isVisible()
    overlay._hide_timer.setInterval(20)
    QTest.qWait(50)
    assert not overlay.isVisible()
    overlay.close()


@pytest.mark.parametrize("position", ["top-left", "top-center", "middle-center", "bottom-right"])
def test_position_presets_anchor_caption_to_screen(qapp, position):
    rect = QGuiApplication.primaryScreen().availableGeometry()
    overlay = CaptionOverlay(Settings(overlay_position=position, overlay_width=240))
    overlay.show_caption(view("原文"))
    if "left" in position:
        assert overlay.x() == rect.left() + 45
    if "center" == position.split("-")[1]:
        assert abs((overlay.x() + overlay.width() // 2) - rect.center().x()) <= 1
    if "right" in position:
        assert overlay.x() + overlay.width() == rect.right() - 44
    if position.startswith("top"):
        assert overlay.y() == rect.top() + 55
    if position.startswith("middle"):
        assert abs((overlay.y() + overlay.height() // 2) - rect.center().y()) <= 1
    if position.startswith("bottom"):
        assert overlay.y() + overlay.height() == rect.bottom() - 55
    overlay.close()


def test_global_hotkey_action_mapping():
    assert action_for_hotkey_id(1) == "compare"
    assert action_for_hotkey_id(2) == "visibility"
    assert action_for_hotkey_id(3) == "language"
    assert action_for_hotkey_id(4) == "pause"
    assert action_for_hotkey_id(999) is None
