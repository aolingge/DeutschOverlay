"""Windows tray application and user-facing controls."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from deutsch_overlay.audio import AudioDeviceError, list_output_devices
from deutsch_overlay.captions import CaptionView
from deutsch_overlay.config import ConfigError, Settings, load_settings, save_settings
from deutsch_overlay.controller import CaptionController
from deutsch_overlay.credentials import AzureCredentialStore
from deutsch_overlay.hotkeys import HotkeyService
from deutsch_overlay.learning_history import CaptionHistory
from deutsch_overlay.overlay import CaptionOverlay
from deutsch_overlay.settings_window import SettingsWindow
from deutsch_overlay.single_instance import SingleInstance


def default_settings_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DeutschOverlay"
    return base / "settings.json"


def startup_settings(saved: Settings) -> Settings:
    """Require a fresh user action for each potentially billed cloud session."""
    return replace(saved, mode="local") if saved.mode == "online" else saved


def audio_self_test(*, source=None, timeout_seconds: float = 5.0) -> tuple[int, str]:
    """Check that this build can open a Windows loopback recorder."""
    import threading
    import numpy as np
    from deutsch_overlay.audio import LoopbackSource

    stop = threading.Event()
    result = {}
    source = source if source is not None else LoopbackSource()

    def capture() -> None:
        try:
            frame = next(source.frames(stop))
            device = source.active_device
            result["message"] = (
                f"音频回采已连接：{device.name if device else '播放设备'}；"
                f"首帧峰值 {float(np.max(np.abs(frame))):.4f}（无播放时可为 0）"
            )
        except Exception as exc:
            result["message"] = f"音频回采失败：{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=capture, name="audio-self-test", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    stop.set()
    if worker.is_alive():
        return 2, "音频回采超时，请检查播放设备和 Windows 音频服务"
    message = result.get("message", "音频回采没有返回结果")
    return (1 if message.startswith("音频回采失败") else 0), message


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
        self.history = CaptionHistory()
        self.window.set_status(startup_message)
        self._visible = True
        self._paused = False
        self._restart_pending = False
        self._restart_scheduled = False
        self._locked = True
        self._last_view: CaptionView | None = None
        self._last_view_received_at: float | None = None
        self._history_suppressed_view: tuple | None = None
        self._shutting_down = False
        self.tray = QSystemTrayIcon(_tray_icon(), self.window)
        self.tray.setToolTip("Deutsch Overlay")
        self._build_tray()
        self._connect_signals()
        self.window.set_runtime_controls(visible=self._visible, paused=self._paused)

    def _build_tray(self) -> None:
        menu = QMenu(self.window)
        menu.addAction("打开设置", self.show_settings)
        menu.addAction("显示/隐藏字幕", self.toggle_visibility)
        menu.addAction("切换原文对照", self.toggle_compare)
        menu.addAction("切换语言", self.cycle_language)
        menu.addAction("暂停/继续识别", self.toggle_pause)
        menu.addAction("解锁/锁定位置", self.toggle_lock)
        menu.addSeparator()
        menu.addAction("退出", self.shutdown)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_settings()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None
        )

    def _connect_signals(self) -> None:
        self.controller.view_changed.connect(self._show_view)
        self.controller.status_changed.connect(self.window.set_status)
        self.controller.level_changed.connect(self.window.set_audio_level)
        self.window.settings_changed.connect(self.apply_settings)
        self.window.credentials_submitted.connect(self.save_credentials)
        self.window.unlock_requested.connect(self.toggle_lock)
        self.window.visibility_requested.connect(self.toggle_visibility)
        self.window.pause_requested.connect(self.toggle_pause)
        self.window.models_help_requested.connect(self._show_model_help)
        self.window.refresh_devices_requested.connect(self.refresh_devices)
        self.window.reset_position_requested.connect(self.reset_position)
        self.window.preview_requested.connect(self.apply_and_preview)
        self.window.exit_requested.connect(self.shutdown)
        self.window.history_dialog.clear_requested.connect(self.clear_history)
        self.overlay.moved.connect(self._save_position)
        self.overlay.compare_requested.connect(self.toggle_compare)
        self.overlay.settings_requested.connect(self.show_settings)
        self.overlay.lock_requested.connect(self.toggle_lock)
        self.hotkeys.action.connect(self._hotkey_action)
        self.hotkeys.failed.connect(self.window.set_status)

    def start(self) -> None:
        tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self.window.close_exits = not tray_available
        if tray_available:
            self.tray.show()
        self.window.show()
        self.hotkeys.start()
        self._start_pipeline()

    def _start_pipeline(self) -> None:
        if self._restart_pending and getattr(self.controller, "running", False):
            self._schedule_restart()
            return
        self._restart_pending = self.controller.start(self.settings) is False
        if self._restart_pending:
            self.window.set_status("正在等待上一段识别会话退出；退出后会自动继续")
            self._schedule_restart()

    def _schedule_restart(self) -> None:
        if not self._restart_scheduled:
            self._restart_scheduled = True
            QTimer.singleShot(250, self._retry_pending_start)

    def _retry_pending_start(self) -> None:
        self._restart_scheduled = False
        if self._shutting_down or self._paused or not self._restart_pending:
            return
        self._start_pipeline()

    def _show_view(self, view: CaptionView) -> None:
        view_key = (view.session_id, view.segment_id, view.timestamp, view.final)
        suppress_history = self._history_suppressed_view == view_key
        if self._history_suppressed_view is not None and not suppress_history:
            self._history_suppressed_view = None
        if not suppress_history and self.history.add(view):
            self.window.history_dialog.set_history(self.history.text())
        if (self._last_view is None or
                (view.session_id, view.segment_id, view.timestamp, view.final) !=
                (self._last_view.session_id, self._last_view.segment_id,
                 self._last_view.timestamp, self._last_view.final)):
            self._last_view_received_at = time.monotonic()
        self._last_view = view
        if self._visible and not self._paused and self._locked:
            self._restore_recent_caption()

    def clear_history(self) -> None:
        if self._last_view is not None and self._last_view.final:
            self._history_suppressed_view = (
                self._last_view.session_id, self._last_view.segment_id,
                self._last_view.timestamp, self._last_view.final,
            )
        else:
            self._history_suppressed_view = None
        self.history.clear()
        self.window.history_dialog.set_history("")

    def _restore_recent_caption(self) -> bool:
        if self._last_view is None or self._last_view_received_at is None:
            return False
        lifetime = (self.settings.fade_seconds if self._last_view.final else
                    max(2.0, self.settings.fade_seconds))
        remaining = lifetime - (time.monotonic() - self._last_view_received_at)
        if remaining > 0:
            self.overlay.show_caption(self._last_view, timeout_seconds=remaining)
            return True
        return False

    def apply_settings(self, updated: Settings) -> bool:
        previous = self.settings
        try:
            save_settings(self.settings_path, updated)
        except OSError:
            self.window.set_status("设置保存失败，请检查应用数据目录")
            return False
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
            self._last_view = None
            self._last_view_received_at = None
            self._start_pipeline()
        elif not pipeline_changed and not self._restart_pending:
            self.window.set_status(
                "快捷操作已保存；还有未应用的设置" if self.window.has_pending_changes() else "设置已保存"
            )
        return True

    def save_credentials(self, region: str, key: str) -> None:
        try:
            self.credential_store.save(region, key)
        except (ValueError, OSError, RuntimeError):
            self.window.set_status("在线凭据未保存；请检查区域、密钥和 Windows 凭据存储")
            return
        if self.settings.mode == "online":
            self.window.set_status("在线凭据已保存；若在线识别已停止，请点击“应用设置”重新开始（可能产生费用）")
        else:
            self.window.set_status("在线凭据已存入 Windows；选择在线模式后才会发送音频")

    def toggle_compare(self) -> None:
        self.apply_settings(replace(self.settings, compare_original=not self.settings.compare_original))

    def toggle_visibility(self) -> None:
        self._visible = not self._visible
        self.window.set_runtime_controls(visible=self._visible, paused=self._paused)
        if not self._visible:
            self.overlay.hide_caption()
            self.window.set_status("字幕已隐藏")
        elif not self._locked:
            self._show_position_hint()
            self.window.set_status("字幕已显示；拖动字幕条调整位置")
        elif self._paused:
            self.window.set_status("字幕显示已开启；识别已暂停")
        elif self._restore_recent_caption():
            self.window.set_status("字幕已显示")
        else:
            self.window.set_status("字幕显示已开启，等待下一句")

    def cycle_language(self) -> None:
        choices = ("auto", "de", "en", "zh")
        next_index = (choices.index(self.settings.language_lock) + 1) % len(choices)
        self.apply_settings(replace(self.settings, language_lock=choices[next_index]))

    def toggle_pause(self) -> None:
        self._paused = not self._paused
        self.window.set_runtime_controls(visible=self._visible, paused=self._paused)
        if self._paused:
            self.overlay.hide_caption()
            self._last_view = None
            self._last_view_received_at = None
            self.controller.pause(True)
        else:
            self._start_pipeline()

    def toggle_lock(self) -> None:
        self._locked = not self._locked
        self.overlay.set_locked(self._locked)
        if self._locked:
            self.overlay.hide_caption()
            if self._visible and not self._paused:
                self._restore_recent_caption()
            self.window.set_status("字幕位置已锁定")
        elif self._visible:
            self._show_position_hint()
            self.window.set_status("拖动字幕条到想要的位置，完成后再次锁定")
        else:
            self.window.set_status("字幕位置已解锁；显示字幕后可拖动")

    def _show_position_hint(self) -> None:
        self.overlay.show_caption(
            CaptionView(0, "position", "拖动字幕条调整位置", None, False, 0),
            persistent=True,
        )

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
        if self.apply_settings(replace(self.settings, overlay_x=None, overlay_y=None,
                                       overlay_position="bottom-center")):
            self.window.set_status("字幕位置已恢复到屏幕下方")

    def preview_caption(self) -> None:
        self.overlay.show_caption(CaptionView(
            0, "preview", "Guten Tag! Ich lerne Deutsch.",
            "你好，我在学习德语。" if self.settings.compare_original else None,
            True, 0.0,
        ), persistent=not self._locked)

    def apply_and_preview(self, updated: Settings) -> None:
        if self.apply_settings(updated):
            self.preview_caption()

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
        if self._shutting_down:
            return
        self._shutting_down = True
        self.hotkeys.stop()
        self.controller.stop()
        self.tray.hide()
        self.overlay.close()
        self.window.close_exits = False
        self.window.close()
        self.window.hide()
        self.application.quit()

    def show_settings(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()


def main() -> None:
    if sys.argv[1:] == ["--audio-self-test"]:
        code, message = audio_self_test()
        report = os.environ.get("DEUTSCH_OVERLAY_SELF_TEST_REPORT")
        if report:
            Path(report).write_text(message + "\n", encoding="utf-8")
        sys.exit(code)
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
    runtime = None
    guard = SingleInstance("DeutschOverlay.settings", lambda: runtime.show_settings())
    if not guard.listen():
        if guard.already_running:
            if not guard.notify_existing():
                QMessageBox.warning(None, "Deutsch Overlay", "程序已经在运行，但暂时无法打开现有设置窗口。")
        else:
            QMessageBox.warning(None, "Deutsch Overlay", f"启动失败：{guard.error_message}")
        return
    try:
        runtime = DesktopApp(application)
        runtime.start()
        sys.exit(application.exec())
    finally:
        guard.close()


if __name__ == "__main__":
    main()
