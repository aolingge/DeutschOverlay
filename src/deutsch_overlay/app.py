"""Windows tray application and user-facing controls."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from deutsch_overlay.audio import AudioDeviceError, list_output_devices
from deutsch_overlay.captions import CaptionView
from deutsch_overlay.config import ConfigError, Settings, load_settings, save_settings
from deutsch_overlay.controller import CaptionController
from deutsch_overlay.credentials import AzureCredentialStore
from deutsch_overlay.hotkeys import HotkeyService
from deutsch_overlay.overlay import CaptionOverlay
from deutsch_overlay.settings_window import SettingsWindow


def default_settings_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DeutschOverlay"
    return base / "settings.json"


def startup_settings(saved: Settings) -> Settings:
    """Require a fresh user action for each potentially billed cloud session."""
    return replace(saved, mode="local") if saved.mode == "online" else saved


def _tray_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#14243c"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#f2d88a"))
    font = painter.font()
    font.setPointSize(22)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "DE")
    painter.end()
    return QIcon(pixmap)


class DesktopApp:
    def __init__(
        self,
        application: QApplication,
        *,
        settings_path: Path | None = None,
        controller=None,
        hotkeys=None,
        credential_store=None,
        devices=None,
    ) -> None:
        self.application = application
        self.application.setQuitOnLastWindowClosed(False)
        self.settings_path = settings_path or default_settings_path()
        startup_message = "准备就绪"
        try:
            saved = load_settings(self.settings_path)
        except ConfigError:
            saved = Settings()
            startup_message = "设置文件无法读取，已使用默认值；原文件未改动"
        self.settings = startup_settings(saved)
        if saved.mode == "online":
            startup_message = "已恢复本地模式；在线模式需要本次手动开启"
        if devices is None:
            try:
                devices = list_output_devices()
            except AudioDeviceError:
                devices = []
                startup_message = "暂时找不到可用的电脑播放设备"

        self.controller = controller or CaptionController()
        self.hotkeys = hotkeys or HotkeyService()
        self.credential_store = credential_store or AzureCredentialStore()
        self.overlay = CaptionOverlay(self.settings)
        self.window = SettingsWindow(self.settings, devices)
        self.window.set_status(startup_message)
        self._visible = True
        self._paused = False
        self._restart_pending = False
        self._locked = True
        self.tray = QSystemTrayIcon(_tray_icon(), self.window)
        self.tray.setToolTip("Deutsch Overlay")
        self._build_tray()
        self._connect_signals()

    def _build_tray(self) -> None:
        menu = QMenu(self.window)
        menu.addAction("打开设置", self.window.show)
        menu.addAction("显示/隐藏字幕", self.toggle_visibility)
        menu.addAction("切换原文对照", self.toggle_compare)
        menu.addAction("切换语言", self.cycle_language)
        menu.addAction("暂停/继续识别", self.toggle_pause)
        menu.addAction("解锁/锁定位置", self.toggle_lock)
        menu.addSeparator()
        menu.addAction("退出", self.shutdown)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.window.show()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None
        )

    def _connect_signals(self) -> None:
        self.controller.view_changed.connect(self._show_view)
        self.controller.status_changed.connect(self.window.set_status)
        self.window.settings_changed.connect(self.apply_settings)
        self.window.credentials_submitted.connect(self.save_credentials)
        self.window.unlock_requested.connect(self.toggle_lock)
        self.window.models_help_requested.connect(self._show_model_help)
        self.window.refresh_devices_requested.connect(self.refresh_devices)
        self.window.reset_position_requested.connect(self.reset_position)
        self.overlay.moved.connect(self._save_position)
        self.hotkeys.action.connect(self._hotkey_action)
        self.hotkeys.failed.connect(self.window.set_status)

    def start(self) -> None:
        self.tray.show()
        self.window.show()
        self.hotkeys.start()
        self._restart_pending = self.controller.start(self.settings) is False

    def _show_view(self, view: CaptionView) -> None:
        if self._visible and not self._paused:
            self.overlay.show_caption(view)

    def apply_settings(self, updated: Settings) -> None:
        previous = self.settings
        try:
            save_settings(self.settings_path, updated)
        except OSError:
            self.window.set_status("设置保存失败，请检查应用数据目录")
            return
        self.settings = updated
        self.window.update_settings(updated)
        self.overlay.set_style(updated)
        if previous.compare_original != updated.compare_original:
            self.controller.set_compare_original(updated.compare_original)
        pipeline_changed = any(
            getattr(previous, name) != getattr(updated, name)
            for name in ("mode", "output_device_id", "language_lock", "online_minutes_limit")
        )
        if (pipeline_changed or self._restart_pending) and not self._paused:
            self.overlay.hide_caption()
            self._restart_pending = self.controller.start(updated) is False
        elif not pipeline_changed and not self._restart_pending:
            self.window.set_status("设置已保存")

    def save_credentials(self, region: str, key: str) -> None:
        try:
            self.credential_store.save(region, key)
        except (ValueError, OSError, RuntimeError):
            self.window.set_status("在线凭据未保存；请检查区域、密钥和 Windows 凭据存储")
            return
        self.window.set_status("在线凭据已存入 Windows；选择在线模式后才会发送音频")

    def toggle_compare(self) -> None:
        self.apply_settings(replace(self.settings, compare_original=not self.settings.compare_original))

    def toggle_visibility(self) -> None:
        self._visible = not self._visible
        if not self._visible:
            self.overlay.hide_caption()
        self.window.set_status("字幕已显示" if self._visible else "字幕已隐藏")

    def cycle_language(self) -> None:
        choices = ("auto", "de", "en", "zh")
        next_index = (choices.index(self.settings.language_lock) + 1) % len(choices)
        self.apply_settings(replace(self.settings, language_lock=choices[next_index]))

    def toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self.overlay.hide_caption()
            self.controller.pause(True)
        else:
            self._restart_pending = self.controller.start(self.settings) is False

    def toggle_lock(self) -> None:
        self._locked = not self._locked
        self.overlay.set_locked(self._locked)
        if self._locked:
            self.overlay.hide_caption()
            self.window.set_status("字幕位置已锁定")
        else:
            self.overlay.show_caption(CaptionView(0, "position", "拖动字幕条调整位置", None, False, 0))
            self.window.set_status("拖动字幕条到想要的位置，完成后再次锁定")

    def _save_position(self, x: int, y: int) -> None:
        self.apply_settings(replace(self.settings, overlay_x=x, overlay_y=y, overlay_position="custom"))

    def refresh_devices(self) -> None:
        try:
            devices = list_output_devices()
        except AudioDeviceError:
            self.window.set_status("刷新失败：找不到可用的电脑播放设备")
            return
        self.window.replace_devices(devices)
        self.window.set_status("播放设备列表已刷新；选择后点击应用设置")

    def reset_position(self) -> None:
        self.apply_settings(replace(self.settings, overlay_x=None, overlay_y=None, overlay_position="bottom-center"))
        self.window.set_status("字幕位置已恢复到屏幕下方")

    def _show_model_help(self) -> None:
        QMessageBox.information(
            self.window,
            "本地模型",
            "完整发行包应包含 DeutschOverlay.exe 旁边的 models 文件夹。"
            "如果提示模型缺失，请重新完整解压发行包，不要只复制 EXE。",
        )

    def _hotkey_action(self, action: str) -> None:
        actions = {
            "compare": self.toggle_compare,
            "visibility": self.toggle_visibility,
            "language": self.cycle_language,
            "pause": self.toggle_pause,
        }
        callback = actions.get(action)
        if callback:
            callback()

    def shutdown(self) -> None:
        self.hotkeys.stop()
        self.controller.stop()
        self.tray.hide()
        self.overlay.close()
        self.window.close()
        self.application.quit()


def main() -> None:
    if sys.argv[1:] == ["--self-test"]:
        from deutsch_overlay.audio import SileroSpeechDetector
        from deutsch_overlay.engines.local import LocalEngine

        try:
            LocalEngine().prepare()
            SileroSpeechDetector()
        except Exception as exc:
            report = os.environ.get("DEUTSCH_OVERLAY_SELF_TEST_REPORT")
            if report:
                Path(report).write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            sys.exit(1)
        sys.exit(0)
    application = QApplication(sys.argv)
    application.setApplicationName("Deutsch Overlay")
    runtime = DesktopApp(application)
    runtime.start()
    sys.exit(application.exec())


if __name__ == "__main__":
    main()
