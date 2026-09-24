import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from deutsch_overlay.app import DesktopApp, startup_settings
from deutsch_overlay.audio import OutputDevice
from deutsch_overlay.config import Settings, load_settings, save_settings


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeController(QObject):
    view_changed = Signal(object)
    status_changed = Signal(str)

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
    runtime.shutdown()


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
