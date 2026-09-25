"""Simple local controls for audio source, language, and caption appearance."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from deutsch_overlay.audio import OutputDevice
from deutsch_overlay.config import Settings


class SettingsWindow(QWidget):
    settings_changed = Signal(object)
    credentials_submitted = Signal(str, str)
    unlock_requested = Signal()
    models_help_requested = Signal()
    refresh_devices_requested = Signal()
    reset_position_requested = Signal()

    def __init__(self, settings: Settings, devices: list[OutputDevice]) -> None:
        super().__init__()
        self._settings = settings
        self.setWindowTitle("Deutsch Overlay · 设置")
        self.resize(460, 510)
        form = QFormLayout()

        self.device_combo = QComboBox()
        self.device_combo.addItem("系统默认播放设备", None)
        for device in devices:
            self.device_combo.addItem(device.name, device.id)
        selected = self.device_combo.findData(settings.output_device_id)
        self.device_combo.setCurrentIndex(max(0, selected))
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh_devices_requested.emit)
        device_row = QHBoxLayout()
        device_row.addWidget(self.device_combo)
        device_row.addWidget(refresh_button)
        form.addRow("电脑声音", device_row)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("本地（默认）", "local")
        self.mode_combo.addItem("在线", "online")
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(settings.mode))
        form.addRow("处理模式", self.mode_combo)

        self.language_combo = QComboBox()
        for label, code in (("自动识别", "auto"), ("德语", "de"), ("英语", "en"), ("中文", "zh")):
            self.language_combo.addItem(label, code)
        self.language_combo.setCurrentIndex(self.language_combo.findData(settings.language_lock))
        form.addRow("声音语言", self.language_combo)

        self.subtitle_combo = QComboBox()
        self.subtitle_combo.addItem("仅德语", False)
        self.subtitle_combo.addItem("德语 + 原文（中文语音时显示中德双语）", True)
        self.subtitle_combo.setCurrentIndex(self.subtitle_combo.findData(settings.compare_original))
        form.addRow("字幕内容", self.subtitle_combo)
        self.position_combo = QComboBox()
        for label, code in (
            ("左上", "top-left"), ("上方居中", "top-center"), ("右上", "top-right"),
            ("左侧居中", "middle-left"), ("屏幕中央", "middle-center"), ("右侧居中", "middle-right"),
            ("左下", "bottom-left"), ("下方居中", "bottom-center"), ("右下", "bottom-right"),
            ("自定义拖动", "custom"),
        ):
            self.position_combo.addItem(label, code)
        self.position_combo.setCurrentIndex(self.position_combo.findData(settings.overlay_position))
        form.addRow("字幕位置", self.position_combo)
        self.speed_hint = QLabel("在线模式锁定声音语言时，可更早显示临时译文；自动识别多语言通常要等整句。")
        self.speed_hint.setWordWrap(True)
        form.addRow("速度提示", self.speed_hint)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(240, 3840)
        self.width_spin.setValue(settings.overlay_width)
        form.addRow("字幕宽度", self.width_spin)
        self.font_spin = QSpinBox()
        self.font_spin.setRange(12, 72)
        self.font_spin.setValue(settings.font_size)
        form.addRow("字体大小", self.font_spin)
        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0.1, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.setValue(settings.opacity)
        form.addRow("背景透明度", self.opacity_spin)
        self.fade_spin = QDoubleSpinBox()
        self.fade_spin.setRange(0, 30)
        self.fade_spin.setValue(settings.fade_seconds)
        form.addRow("无语音后隐藏（秒）", self.fade_spin)
        self.online_limit_spin = QSpinBox()
        self.online_limit_spin.setRange(1, 1440)
        self.online_limit_spin.setValue(settings.online_minutes_limit)
        form.addRow("每日在线上限（分钟）", self.online_limit_spin)

        self.region_input = QLineEdit()
        self.region_input.setPlaceholderText("Azure 区域，例如 eastasia")
        form.addRow("Azure 区域", self.region_input)
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("只存入 Windows 凭据管理器")
        form.addRow("Azure 密钥", self.key_input)

        self.status_label = QLabel("准备就绪")
        self.status_label.setWordWrap(True)
        apply_button = QPushButton("应用设置")
        apply_button.clicked.connect(self._apply)
        save_key_button = QPushButton("保存在线凭据")
        save_key_button.clicked.connect(self._submit_credentials)
        unlock_button = QPushButton("解锁/锁定字幕位置")
        unlock_button.clicked.connect(self.unlock_requested.emit)
        reset_position_button = QPushButton("字幕位置复位")
        reset_position_button.clicked.connect(self.reset_position_requested.emit)
        models_button = QPushButton("模型说明")
        models_button.clicked.connect(self.models_help_requested.emit)
        buttons = QHBoxLayout()
        buttons.addWidget(apply_button)
        buttons.addWidget(save_key_button)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(unlock_button)
        layout.addWidget(reset_position_button)
        layout.addWidget(models_button)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)
        layout.addWidget(QLabel("快捷键：Ctrl+Alt+1 原文；2 显隐；3 语言；4 暂停"))

    def _apply(self) -> None:
        settings = replace(
            self._settings,
            output_device_id=self.device_combo.currentData(),
            mode=self.mode_combo.currentData(),
            language_lock=self.language_combo.currentData(),
            compare_original=self.subtitle_combo.currentData(),
            overlay_position=self.position_combo.currentData(),
            overlay_x=self._settings.overlay_x if self.position_combo.currentData() == "custom" else None,
            overlay_y=self._settings.overlay_y if self.position_combo.currentData() == "custom" else None,
            overlay_width=self.width_spin.value(),
            font_size=self.font_spin.value(),
            opacity=self.opacity_spin.value(),
            fade_seconds=self.fade_spin.value(),
            online_minutes_limit=self.online_limit_spin.value(),
        )
        self._settings = settings
        self.settings_changed.emit(settings)

    def _submit_credentials(self) -> None:
        region = self.region_input.text().strip()
        key = self.key_input.text().strip()
        self.key_input.clear()
        self.credentials_submitted.emit(region, key)

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def update_settings(self, settings: Settings) -> None:
        self._settings = settings
        self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(settings.output_device_id)))
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(settings.mode))
        self.language_combo.setCurrentIndex(self.language_combo.findData(settings.language_lock))
        self.subtitle_combo.setCurrentIndex(self.subtitle_combo.findData(settings.compare_original))
        self.position_combo.setCurrentIndex(self.position_combo.findData(settings.overlay_position))
        self.width_spin.setValue(settings.overlay_width)
        self.font_spin.setValue(settings.font_size)
        self.opacity_spin.setValue(settings.opacity)
        self.fade_spin.setValue(settings.fade_seconds)
        self.online_limit_spin.setValue(settings.online_minutes_limit)

    def replace_devices(self, devices: list[OutputDevice]) -> None:
        selected = self.device_combo.currentData()
        self.device_combo.clear()
        self.device_combo.addItem("系统默认播放设备", None)
        for device in devices:
            self.device_combo.addItem(device.name, device.id)
        self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(selected)))
