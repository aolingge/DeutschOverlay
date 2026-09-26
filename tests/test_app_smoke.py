import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from deutsch_overlay.app import DesktopApp, audio_self_test, startup_settings
from deutsch_overlay.audio import OutputDevice
from deutsch_overlay.captions import CaptionView
from deutsch_overlay.config import Settings, load_settings, save_settings
from deutsch_overlay.settings_window import STYLE_PRESETS, SettingsWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeController(QObject):
    view_changed = Signal(object)
    status_changed = Signal(str)
    level_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.started = []
        self.stopped = 0
        self.comparison = []

    def start(self, settings):
        self.started.append(settings)

    def stop(self):
        self.stopped += 1

    def set_compare_original(self, enabled):
        self.comparison.append(enabled)


class FakeHotkeys(QObject):
    action = Signal(str)
    failed = Signal(str)

    def start(self):
        pass

    def stop(self):
        pass


class FakeCredentials:
    def __init__(self):
        self.saved = []

    def save(self, region, key):
        self.saved.append((region, key))

    def get(self):
        return None


def test_startup_does_not_auto_resume_paid_mode():
    assert startup_settings(Settings(mode="online")).mode == "local"


def test_audio_self_test_checks_packaged_capture_without_requiring_playback():
    import numpy as np

    class Source:
        active_device = OutputDevice("speaker", "Headset", True)

        def frames(self, _stop):
            yield np.zeros(1600, dtype=np.float32)

    code, report = audio_self_test(source=Source())
    assert code == 0
    assert "Headset" in report


def test_audio_self_test_times_out_when_capture_backend_stalls():
    from threading import Event

    released = Event()

    class StalledSource:
        def frames(self, stop):
            released.wait(1)
            if not stop.is_set():
                yield None

    try:
        code, report = audio_self_test(source=StalledSource(), timeout_seconds=0.02)
        assert code != 0
        assert "超时" in report
    finally:
        released.set()


def test_apply_settings_persists_and_restarts_only_pipeline_changes(qapp, tmp_path):
    path = tmp_path / "settings.json"
    controller = FakeController()
    runtime = DesktopApp(
        qapp, settings_path=path, controller=controller,
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    initial = runtime.settings
    runtime.apply_settings(replace(initial, font_size=31, compare_original=True))
    assert load_settings(path).font_size == 31
    assert controller.started == []
    assert controller.comparison == [True]
    runtime.apply_settings(replace(runtime.settings, language_lock="de"))
    assert len(controller.started) == 1
    runtime.shutdown()


def test_cloud_credentials_are_saved_only_by_explicit_form_action(qapp, tmp_path):
    credentials = FakeCredentials()
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=credentials, devices=[],
    )
    assert credentials.saved == []
    runtime.save_credentials("eastasia", "a" * 32)
    assert credentials.saved == [("eastasia", "a" * 32)]
    runtime.shutdown()


def test_audio_input_state_is_visible_in_settings(qapp, tmp_path):
    controller = FakeController()
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=controller,
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        controller.level_changed.emit(0.0)
        assert "未检测到" in runtime.window.audio_level_label.text()
        controller.level_changed.emit(0.001)
        assert "正在接收" in runtime.window.audio_level_label.text()
        controller.level_changed.emit(0.1)
        assert "正在接收" in runtime.window.audio_level_label.text()
        controller.level_changed.emit(None)
        assert "等待" in runtime.window.audio_level_label.text()
    finally:
        runtime.shutdown()


def test_learning_history_can_review_copy_and_clear_final_captions(qapp, tmp_path):
    controller = FakeController()
    path = tmp_path / "settings.json"
    runtime = DesktopApp(
        qapp, settings_path=path, controller=controller,
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        preview = CaptionView(1, "one", "Guten Tag", None, False, 1.0,
                              source_original="你好")
        controller.view_changed.emit(preview)
        assert runtime.window.history_dialog.text.toPlainText() == ""
        controller.view_changed.emit(replace(preview, final=True))
        content = runtime.window.history_dialog.text.toPlainText()
        assert "Guten Tag" in content and "你好" in content
        assert not path.exists()
        runtime.window.history_dialog.copy_button.click()
        assert qapp.clipboard().text() == content
        runtime.window.history_dialog.clear_button.click()
        assert runtime.window.history_dialog.text.toPlainText() == ""
        assert runtime.history.entries == ()
        controller.view_changed.emit(replace(preview, final=True))
        assert runtime.history.entries == ()
        controller.view_changed.emit(CaptionView(1, "two", "Guten Abend", None, True, 2.0))
        assert len(runtime.history.entries) == 1
    finally:
        runtime.shutdown()


def test_closing_settings_also_hides_learning_history(qapp, tmp_path):
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        runtime.window.show()
        runtime.window.history_dialog.show()
        qapp.processEvents()
        assert runtime.window.history_dialog.isVisible()
        runtime.window.close()
        qapp.processEvents()
        assert not runtime.window.history_dialog.isVisible()
    finally:
        runtime.shutdown()


def test_loaded_online_setting_is_local_for_new_session(qapp, tmp_path):
    path = tmp_path / "settings.json"
    save_settings(path, Settings(mode="online"))
    runtime = DesktopApp(
        qapp, settings_path=path, controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    assert runtime.settings.mode == "local"
    runtime.shutdown()


def test_device_refresh_and_reset_position(qapp, tmp_path, monkeypatch):
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    monkeypatch.setattr("deutsch_overlay.app.list_output_devices", lambda: [OutputDevice("new", "USB Headset", True)])
    runtime.refresh_devices()
    assert runtime.window.device_combo.findData("new") >= 0
    runtime.apply_settings(replace(runtime.settings, overlay_x=500, overlay_y=500))
    runtime.reset_position()
    assert runtime.settings.overlay_x is None and runtime.settings.overlay_y is None
    assert runtime.settings.overlay_position == "bottom-center"
    runtime.shutdown()


def test_missing_saved_output_stays_selected_until_user_changes_it(qapp, tmp_path):
    path = tmp_path / "settings.json"
    save_settings(path, Settings(output_device_id="unplugged-headset"))
    runtime = DesktopApp(
        qapp, settings_path=path, controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        combo = runtime.window.device_combo
        assert combo.currentData() == "unplugged-headset"
        assert "不可用" in combo.currentText()
        runtime.window.replace_devices([OutputDevice("other", "Speakers", True)])
        assert combo.currentData() == "unplugged-headset"
        runtime.window._apply()
        assert load_settings(path).output_device_id == "unplugged-headset"
        runtime.window.replace_devices([OutputDevice("unplugged-headset", "Headset", True)])
        assert combo.currentData() == "unplugged-headset"
        assert combo.currentText() == "Headset"
    finally:
        runtime.shutdown()


def test_missing_tray_closing_settings_exits_cleanly(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("deutsch_overlay.app.QSystemTrayIcon.isSystemTrayAvailable", lambda: False)
    controller = FakeController()
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=controller,
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    runtime.start()
    assert runtime.window.isVisible()
    runtime.window.close()
    qapp.processEvents()
    assert controller.stopped >= 1
    assert not runtime.window.isVisible()


def test_display_mode_and_position_choices_save_without_restarting_audio(qapp, tmp_path):
    controller = FakeController()
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=controller,
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    runtime.window.subtitle_combo.setCurrentIndex(runtime.window.subtitle_combo.findData(True))
    runtime.window.position_combo.setCurrentIndex(runtime.window.position_combo.findData("top-right"))
    runtime.window._apply()
    saved = load_settings(runtime.settings_path)
    assert saved.compare_original is True
    assert saved.overlay_position == "top-right"
    assert controller.started == []
    runtime._save_position(25, 35)
    assert load_settings(runtime.settings_path).overlay_position == "custom"
    runtime.shutdown()


def test_controller_caption_reaches_overlay_and_visibility_restores_last_caption(qapp, tmp_path):
    controller = FakeController()
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=controller,
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        controller.view_changed.emit(CaptionView(1, "caption", "Guten Tag", None, True, 1.0))
        qapp.processEvents()
        assert runtime.overlay.isVisible()
        assert runtime.overlay.primary_label.text() == "Guten Tag"
        runtime.toggle_visibility()
        assert not runtime.overlay.isVisible()
        runtime.toggle_visibility()
        assert runtime.overlay.isVisible()
        assert runtime.overlay.primary_label.text() == "Guten Tag"
    finally:
        runtime.shutdown()


def test_hidden_caption_does_not_return_after_its_display_time_expires(qapp, tmp_path):
    from PySide6.QtTest import QTest

    path = tmp_path / "settings.json"
    save_settings(path, Settings(fade_seconds=0.02))
    controller = FakeController()
    runtime = DesktopApp(
        qapp, settings_path=path, controller=controller,
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        controller.view_changed.emit(CaptionView(1, "caption", "Guten Tag", None, True, 1.0))
        qapp.processEvents()
        runtime.toggle_visibility()
        QTest.qWait(80)
        runtime.toggle_visibility()
        assert not runtime.overlay.isVisible()
        assert "等待下一句" in runtime.window.status_label.text()
    finally:
        runtime.shutdown()


def test_restored_caption_keeps_only_its_remaining_display_time(qapp, tmp_path):
    from PySide6.QtTest import QTest

    path = tmp_path / "settings.json"
    save_settings(path, Settings(fade_seconds=0.16))
    runtime = DesktopApp(
        qapp, settings_path=path, controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        view = CaptionView(1, "caption", "Guten Tag", None, True, 1.0)
        runtime._show_view(view)
        runtime.toggle_visibility()
        QTest.qWait(100)
        runtime.toggle_visibility()
        assert runtime.overlay.isVisible()
        QTest.qWait(90)
        assert not runtime.overlay.isVisible()
        runtime._show_view(view)  # The same segment may be re-rendered when comparison changes.
        assert not runtime.overlay.isVisible()
    finally:
        runtime.shutdown()


def test_final_caption_restarts_display_time_after_provisional_with_same_timestamp(qapp, tmp_path):
    from PySide6.QtTest import QTest

    path = tmp_path / "settings.json"
    save_settings(path, Settings(fade_seconds=0.12))
    runtime = DesktopApp(
        qapp, settings_path=path, controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        runtime._show_view(CaptionView(1, "same", "Guten", None, False, 1.0))
        QTest.qWait(60)
        runtime._show_view(CaptionView(1, "same", "Guten Tag", None, True, 1.0))
        runtime.toggle_visibility()
        QTest.qWait(80)
        runtime.toggle_visibility()
        assert runtime.overlay.isVisible()
        assert runtime.overlay.primary_label.text() == "Guten Tag"
    finally:
        runtime.shutdown()


def test_locking_position_restores_recent_caption(qapp, tmp_path):
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        runtime._show_view(CaptionView(1, "caption", "Guten Tag", None, True, 1.0))
        runtime.toggle_lock()
        assert "拖动字幕条" in runtime.overlay.primary_label.text()
        runtime.toggle_lock()
        assert runtime.overlay.isVisible()
        assert runtime.overlay.primary_label.text() == "Guten Tag"
    finally:
        runtime.shutdown()


def test_unlocked_position_hint_stays_visible_until_locked(qapp, tmp_path):
    from PySide6.QtTest import QTest

    path = tmp_path / "settings.json"
    save_settings(path, Settings(fade_seconds=0.02))
    runtime = DesktopApp(
        qapp, settings_path=path, controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        runtime.toggle_lock()
        QTest.qWait(80)
        assert runtime.overlay.isVisible()
        assert "拖动字幕条" in runtime.overlay.primary_label.text()
        runtime.controller.view_changed.emit(CaptionView(1, "spoken", "Guten Tag", None, True, 1.0))
        qapp.processEvents()
        assert "拖动字幕条" in runtime.overlay.primary_label.text()
        runtime.toggle_lock()
        assert runtime.overlay.primary_label.text() == "Guten Tag"
        QTest.qWait(80)
        assert not runtime.overlay.isVisible()
    finally:
        runtime.shutdown()


def test_tray_open_settings_restores_minimized_window(qapp, tmp_path):
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        runtime.window.showMinimized()
        qapp.processEvents()
        assert runtime.window.isMinimized()
        runtime.tray.contextMenu().actions()[0].trigger()
        qapp.processEvents()
        assert runtime.window.isVisible() and not runtime.window.isMinimized()
    finally:
        runtime.shutdown()


def test_unlocked_preview_remains_draggable_after_caption_timeout(qapp, tmp_path):
    from PySide6.QtTest import QTest

    path = tmp_path / "settings.json"
    save_settings(path, Settings(fade_seconds=0.02))
    runtime = DesktopApp(
        qapp, settings_path=path, controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        runtime.toggle_lock()
        runtime.preview_caption()
        QTest.qWait(80)
        assert runtime.overlay.isVisible()
        assert runtime.overlay.primary_label.text()
    finally:
        runtime.shutdown()


def test_unlocking_position_while_hidden_keeps_overlay_hidden(qapp, tmp_path):
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        runtime.toggle_visibility()
        runtime.toggle_lock()
        assert not runtime.overlay.isVisible()
        runtime.toggle_visibility()
        assert runtime.overlay.isVisible()
        assert "拖动字幕条" in runtime.overlay.primary_label.text()
    finally:
        runtime.shutdown()


def test_background_preset_custom_color_and_live_preview(qapp, tmp_path, monkeypatch):
    controller = FakeController()
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=controller,
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    window = runtime.window
    window.style_combo.setCurrentIndex(window.style_combo.findData("light"))
    window._apply()
    assert load_settings(runtime.settings_path).background_color == "#F5F5F5"
    assert "#111111" in runtime.overlay.primary_label.styleSheet()
    assert controller.started == []
    monkeypatch.setattr("deutsch_overlay.settings_window.QColorDialog.getColor", lambda *_a, **_kw: QColor("#123456"))
    window.background_button.click()
    assert window.style_combo.currentData() == "custom"
    window._apply()
    assert load_settings(runtime.settings_path).background_color == "#123456".upper()
    assert controller.started == []
    window.subtitle_combo.setCurrentIndex(window.subtitle_combo.findData(True))
    window._apply_and_preview()
    assert runtime.overlay.isVisible()
    assert "Guten Tag" in runtime.overlay.primary_label.text()
    assert "你好" in runtime.overlay.secondary_label.text()
    runtime.shutdown()


def test_failed_save_does_not_show_misleading_style_preview(qapp, tmp_path, monkeypatch):
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    monkeypatch.setattr("deutsch_overlay.app.save_settings", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))
    runtime.window._apply_and_preview()
    assert not runtime.overlay.isVisible()
    assert "保存失败" in runtime.window.status_label.text()
    assert runtime.window._settings == runtime.settings
    runtime.shutdown()


def test_failed_apply_keeps_saved_position_as_candidate_baseline(qapp, tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    save_settings(path, Settings(overlay_x=31, overlay_y=53, overlay_position="custom"))
    runtime = DesktopApp(
        qapp, settings_path=path, controller=FakeController(),
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        monkeypatch.setattr("deutsch_overlay.app.save_settings", lambda *_args: (_ for _ in ()).throw(OSError()))
        runtime.window.font_spin.setValue(35)
        runtime.window._apply()
        assert runtime.window._settings == load_settings(path)
        assert runtime.window._candidate_settings().overlay_x == 31
        assert runtime.window._candidate_settings().overlay_y == 53
    finally:
        runtime.shutdown()


def test_every_background_preset_reaches_saved_settings(qapp):
    window = SettingsWindow(Settings(), [])
    selected = []
    window.settings_changed.connect(selected.append)
    for code, expected in STYLE_PRESETS.items():
        window.style_combo.setCurrentIndex(window.style_combo.findData(code))
        window._apply()
        assert all(getattr(selected[-1], key) == value for key, value in expected.items())
    window.show()
    qapp.processEvents()
    assert window.settings_scroll.horizontalScrollBar().maximum() == 0
    assert window.settings_scroll.verticalScrollBar().maximum() > 0
    window.close()


def test_same_settings_retry_after_previous_worker_blocks_restart(qapp, tmp_path):
    class BusyController(FakeController):
        def start(self, settings):
            super().start(settings)
            return len(self.started) > 1

    controller = BusyController()
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=controller,
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    updated = replace(runtime.settings, language_lock="de")
    runtime.apply_settings(updated)
    runtime.apply_settings(updated)
    assert len(controller.started) == 2
    runtime.shutdown()


def test_pending_pipeline_restart_runs_after_old_worker_exits(qapp, tmp_path):
    from PySide6.QtTest import QTest

    class SlowShutdownController(FakeController):
        running = True

        def start(self, settings):
            super().start(settings)
            return not self.running

    controller = SlowShutdownController()
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=controller,
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        runtime.apply_settings(replace(runtime.settings, mode="online"))
        assert runtime._restart_pending
        assert len(controller.started) == 1
        QTest.qWait(300)
        assert len(controller.started) == 1
        controller.running = False
        for _ in range(20):
            QTest.qWait(50)
            if not runtime._restart_pending:
                break
        assert not runtime._restart_pending
        assert len(controller.started) == 2
        assert controller.started[-1].mode == "online"
    finally:
        runtime.shutdown()


def test_pending_online_restart_uses_latest_local_choice(qapp, tmp_path):
    from PySide6.QtTest import QTest

    class SlowShutdownController(FakeController):
        running = True

        def start(self, settings):
            super().start(settings)
            return not self.running

    controller = SlowShutdownController()
    runtime = DesktopApp(
        qapp, settings_path=tmp_path / "settings.json", controller=controller,
        hotkeys=FakeHotkeys(), credential_store=FakeCredentials(), devices=[],
    )
    try:
        runtime.apply_settings(replace(runtime.settings, mode="online"))
        runtime.apply_settings(replace(runtime.settings, mode="local", language_lock="de"))
        controller.running = False
        for _ in range(20):
            QTest.qWait(50)
            if not runtime._restart_pending:
                break
        assert not runtime._restart_pending
        assert controller.started[-1].mode == "local"
        assert controller.started[-1].language_lock == "de"
        assert len(controller.started) == 2
    finally:
        runtime.shutdown()
