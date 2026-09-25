"""Simple local controls for audio source, language, and caption appearance."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from deutsch_overlay.audio import OutputDevice
from deutsch_overlay.config import Settings


STYLE_PRESETS = {
    "dark": dict(background_color="#000000", primary_color="#FFFFFF", secondary_color="#DDDDDD",
                 border_color="#FFFFFF", border_width=0, corner_radius=8,
                 padding_horizontal=18, padding_vertical=9, opacity=0.70),
    "light": dict(background_color="#F5F5F5", primary_color="#111111", secondary_color="#333333",
                  border_color="#CCCCCC", border_width=1, corner_radius=8,
                  padding_horizontal=18, padding_vertical=9, opacity=0.90),
    "clear": dict(background_color="#000000", primary_color="#FFFFFF", secondary_color="#DDDDDD",
                  border_color="#FFFFFF", border_width=0, corner_radius=0,
                  padding_horizontal=18, padding_vertical=9, opacity=0.0),
    "outlined": dict(background_color="#101820", primary_color="#FFFFFF", secondary_color="#E5E5E5",
                     border_color="#F2D88A", border_width=2, corner_radius=12,
                     padding_horizontal=20, padding_vertical=10, opacity=0.85),
}


class SettingsWindow(QWidget):
    settings_changed = Signal(object)
    credentials_submitted = Signal(str, str)
    unlock_requested = Signal()
    models_help_requested = Signal()
    refresh_devices_requested = Signal()
    reset_position_requested = Signal()
    preview_requested = Signal(object)

    def __init__(self, settings: Settings, devices: list[OutputDevice]) -> None:
        super().__init__()
        self._settings = settings
        self.setWindowTitle("Deutsch Overlay · 设置")
        self.resize(520, 650)
        self._updating_style = False
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
        self.audio_level_label = QLabel("等待声音采集")
        self.audio_level_label.setWordWrap(True)
        form.addRow("输入状态", self.audio_level_label)

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
        self.opacity_spin.setRange(0, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.setValue(settings.opacity)
        form.addRow("背景不透明度", self.opacity_spin)

        self.style_combo = QComboBox()
        for label, code in (("深色", "dark"), ("浅色", "light"),
                            ("透明文字", "clear"), ("描边", "outlined"), ("自定义", "custom")):
            self.style_combo.addItem(label, code)
        form.addRow("背景样式", self.style_combo)
        self.background_button = self._color_button(settings.background_color, "背景")
        self.primary_button = self._color_button(settings.primary_color, "德语文字")
        self.secondary_button = self._color_button(settings.secondary_color, "原文")
        self.border_button = self._color_button(settings.border_color, "边框")
        for label, button in (("背景颜色", self.background_button), ("德语文字颜色", self.primary_button),
                              ("原文颜色", self.secondary_button), ("边框颜色", self.border_button)):
            form.addRow(label, button)
        self.border_spin = QSpinBox()
        self.border_spin.setRange(0, 8)
        self.border_spin.setValue(settings.border_width)
        form.addRow("边框宽度", self.border_spin)
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(0, 32)
        self.radius_spin.setValue(settings.corner_radius)
        form.addRow("圆角半径", self.radius_spin)
        self.padding_x_spin = QSpinBox()
        self.padding_x_spin.setRange(0, 40)
        self.padding_x_spin.setValue(settings.padding_horizontal)
        form.addRow("左右留白", self.padding_x_spin)
        self.padding_y_spin = QSpinBox()
        self.padding_y_spin.setRange(0, 24)
        self.padding_y_spin.setValue(settings.padding_vertical)
        form.addRow("上下留白", self.padding_y_spin)
        self.style_combo.currentIndexChanged.connect(self._apply_preset)
        for spin in (self.opacity_spin, self.border_spin, self.radius_spin,
                     self.padding_x_spin, self.padding_y_spin):
            spin.valueChanged.connect(self._mark_custom)
        self._select_matching_preset(settings)
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
        preview_button = QPushButton("应用并预览")
        preview_button.clicked.connect(self._apply_and_preview)
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
        buttons.addWidget(preview_button)
        buttons.addWidget(save_key_button)
        panel = QWidget()
        panel.setLayout(form)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setWidget(panel)
        layout = QVBoxLayout(self)
        layout.addWidget(self.settings_scroll, 1)
        layout.addWidget(unlock_button)
        layout.addWidget(reset_position_button)
        layout.addWidget(models_button)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)
        layout.addWidget(QLabel("快捷键：Ctrl+Alt+1 原文；2 显隐；3 语言；4 暂停"))

    @staticmethod
    def _set_button_color(button: QPushButton, color: str) -> None:
        button.setProperty("color", color)
        button.setText(color)
        value = QColor(color)
        foreground = "#111111" if value.lightness() > 150 else "#FFFFFF"
        button.setStyleSheet(f"background-color: {color}; color: {foreground};")

    def _color_button(self, color: str, name: str) -> QPushButton:
        button = QPushButton()
        self._set_button_color(button, color)
        button.clicked.connect(lambda: self._pick_color(button, name))
        return button

    def _pick_color(self, button: QPushButton, name: str) -> None:
        color = QColorDialog.getColor(QColor(button.property("color")), self, f"选择{name}颜色")
        if color.isValid():
            self._set_button_color(button, color.name().upper())
            self._mark_custom()

    def _mark_custom(self) -> None:
        if not self._updating_style:
            self.style_combo.blockSignals(True)
            self.style_combo.setCurrentIndex(self.style_combo.findData("custom"))
            self.style_combo.blockSignals(False)

    def _apply_preset(self) -> None:
        preset = STYLE_PRESETS.get(self.style_combo.currentData())
        if preset is None:
            return
        self._updating_style = True
        try:
            for name, button in (("background_color", self.background_button),
                                 ("primary_color", self.primary_button),
                                 ("secondary_color", self.secondary_button),
                                 ("border_color", self.border_button)):
                self._set_button_color(button, preset[name])
            for name, spin in (("border_width", self.border_spin), ("corner_radius", self.radius_spin),
                               ("padding_horizontal", self.padding_x_spin),
                               ("padding_vertical", self.padding_y_spin), ("opacity", self.opacity_spin)):
                spin.setValue(preset[name])
        finally:
            self._updating_style = False

    def _select_matching_preset(self, settings: Settings) -> None:
        code = next((name for name, values in STYLE_PRESETS.items()
                     if all(getattr(settings, key) == value for key, value in values.items())), "custom")
        self.style_combo.blockSignals(True)
        self.style_combo.setCurrentIndex(self.style_combo.findData(code))
        self.style_combo.blockSignals(False)

    def _candidate_settings(self) -> Settings:
        return replace(
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
            background_color=self.background_button.property("color"),
            primary_color=self.primary_button.property("color"),
            secondary_color=self.secondary_button.property("color"),
            border_color=self.border_button.property("color"),
            border_width=self.border_spin.value(),
            corner_radius=self.radius_spin.value(),
            padding_horizontal=self.padding_x_spin.value(),
            padding_vertical=self.padding_y_spin.value(),
            fade_seconds=self.fade_spin.value(),
            online_minutes_limit=self.online_limit_spin.value(),
        )

    def _apply(self) -> None:
        settings = self._candidate_settings()
        self._settings = settings
        self.settings_changed.emit(settings)

    def _apply_and_preview(self) -> None:
        settings = self._candidate_settings()
        self._settings = settings
        self.preview_requested.emit(settings)

    def _submit_credentials(self) -> None:
        region = self.region_input.text().strip()
        key = self.key_input.text().strip()
        self.key_input.clear()
        self.credentials_submitted.emit(region, key)

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def set_audio_level(self, level: float | None) -> None:
        if level is None:
            self.audio_level_label.setText("等待声音采集")
        elif level < 0.003:
            self.audio_level_label.setText("未检测到电脑播放声；播放视频后仍如此，请检查播放设备和系统音量")
        else:
            self.audio_level_label.setText("正在接收电脑播放声")

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
        for name, button in (("background_color", self.background_button),
                             ("primary_color", self.primary_button),
                             ("secondary_color", self.secondary_button),
                             ("border_color", self.border_button)):
            self._set_button_color(button, getattr(settings, name))
        self.border_spin.setValue(settings.border_width)
        self.radius_spin.setValue(settings.corner_radius)
        self.padding_x_spin.setValue(settings.padding_horizontal)
        self.padding_y_spin.setValue(settings.padding_vertical)
        self._select_matching_preset(settings)
        self.fade_spin.setValue(settings.fade_seconds)
        self.online_limit_spin.setValue(settings.online_minutes_limit)

    def replace_devices(self, devices: list[OutputDevice]) -> None:
        selected = self.device_combo.currentData()
        self.device_combo.clear()
        self.device_combo.addItem("系统默认播放设备", None)
        for device in devices:
            self.device_combo.addItem(device.name, device.id)
        self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(selected)))
