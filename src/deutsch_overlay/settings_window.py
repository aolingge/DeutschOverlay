"""The user-facing controls for captions, appearance and online processing."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from deutsch_overlay.audio import OutputDevice
from deutsch_overlay.choice_group import ChoiceGroup
from deutsch_overlay.config import Settings
from deutsch_overlay.learning_history_dialog import LearningHistoryDialog
from deutsch_overlay.ui_theme import SETTINGS_STYLE


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

EDITABLE_FIELDS = (
    "mode", "language_lock", "compare_original", "output_device_id", "overlay_position",
    "overlay_width", "font_size", "opacity", "background_color", "primary_color",
    "secondary_color", "border_color", "border_width", "corner_radius",
    "padding_horizontal", "padding_vertical", "fade_seconds", "online_minutes_limit",
)


class SettingsWindow(QWidget):
    settings_changed = Signal(object)
    credentials_submitted = Signal(str, str)
    unlock_requested = Signal()
    visibility_requested = Signal()
    pause_requested = Signal()
    models_help_requested = Signal()
    refresh_devices_requested = Signal()
    reset_position_requested = Signal()
    preview_requested = Signal(object)
    exit_requested = Signal()

    def __init__(self, settings: Settings, devices: list[OutputDevice]) -> None:
        super().__init__()
        self._settings = settings
        self._devices = list(devices)
        self._updating_style = False
        self.close_exits = False
        self.setObjectName("settingsRoot")
        self.setWindowTitle("Deutsch Overlay · 设置")
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        if available is None:
            self.setMinimumSize(720, 610)
            self.resize(860, 830)
        else:
            width = min(860, max(1, available.width() - 40))
            height = min(830, max(1, available.height() - 80))
            self.setMinimumSize(min(720, width), min(610, height))
            self.resize(width, height)
            self.move(available.left() + (available.width() - width) // 2,
                      available.top() + (available.height() - height) // 2)
        self.setFont(QFont("Segoe UI Variable", 10))
        self.setStyleSheet(SETTINGS_STYLE)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_content(), 1)
        self._show_page("subtitles")

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget(self)
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(188)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 25, 16, 18)
        layout.setSpacing(8)
        brand = QLabel("Deutsch Overlay")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        subtitle = QLabel("实时德语字幕")
        subtitle.setObjectName("brandSubtitle")
        layout.addWidget(subtitle)
        layout.addSpacing(28)
        section = QLabel("工作空间")
        section.setObjectName("sidebarHint")
        layout.addWidget(section)
        self.nav_subtitles = self._nav_button("字幕", "subtitles")
        self.nav_appearance = self._nav_button("外观", "appearance")
        self.nav_online = self._nav_button("在线服务", "online")
        for button in (self.nav_subtitles, self.nav_appearance, self.nav_online):
            layout.addWidget(button)
        layout.addStretch(1)
        self.history_dialog = LearningHistoryDialog(self)
        history_button = QPushButton("学习记录")
        history_button.setObjectName("sideAction")
        history_button.clicked.connect(self.history_dialog.show)
        layout.addWidget(history_button)
        models_button = QPushButton("本地模型说明")
        models_button.setObjectName("sideAction")
        models_button.clicked.connect(self.models_help_requested.emit)
        layout.addWidget(models_button)
        layout.addSpacing(10)
        hotkeys = QLabel("快捷键  Ctrl + Alt + 1~4\n字幕 · 显隐 · 语言 · 暂停")
        hotkeys.setObjectName("sidebarHint")
        hotkeys.setWordWrap(True)
        layout.addWidget(hotkeys)
        return sidebar

    def _nav_button(self, text: str, page: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("navButton")
        button.setCheckable(True)
        button.clicked.connect(lambda: self._show_page(page))
        return button

    def _build_content(self) -> QWidget:
        content = QWidget(self)
        content.setObjectName("contentPane")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QWidget(content)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(28, 22, 28, 12)
        header_layout.setSpacing(4)
        self.page_title = QLabel()
        self.page_title.setObjectName("pageTitle")
        self.page_description = QLabel()
        self.page_description.setObjectName("pageDescription")
        self.page_description.setWordWrap(True)
        header_layout.addWidget(self.page_title)
        header_layout.addWidget(self.page_description)
        layout.addWidget(header)

        self.pages = QStackedWidget(content)
        self.settings_scroll = self._scroll_page(self._build_subtitles_page())
        self.pages.addWidget(self.settings_scroll)
        self.pages.addWidget(self._scroll_page(self._build_appearance_page()))
        self.pages.addWidget(self._scroll_page(self._build_online_page()))
        layout.addWidget(self.pages, 1)

        footer = QWidget(content)
        footer.setObjectName("footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(26, 13, 28, 13)
        footer_layout.setSpacing(9)
        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        footer_layout.addWidget(self.status_label, 1)
        self.apply_button = QPushButton("应用设置")
        self.apply_button.clicked.connect(self._apply)
        footer_layout.addWidget(self.apply_button)
        self.preview_button = QPushButton("应用并预览")
        self.preview_button.setObjectName("primaryButton")
        self.preview_button.clicked.connect(self._apply_and_preview)
        footer_layout.addWidget(self.preview_button)
        layout.addWidget(footer)
        return content

    @staticmethod
    def _scroll_page(body: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        return scroll

    @staticmethod
    def _page_body() -> tuple[QWidget, QVBoxLayout]:
        body = QWidget()
        body.setObjectName("pageBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 7, 28, 20)
        layout.setSpacing(13)
        return body, layout

    @staticmethod
    def _card(title: str, hint: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 17)
        layout.setSpacing(9)
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
        if hint:
            note = QLabel(hint)
            note.setObjectName("hint")
            note.setWordWrap(True)
            layout.addWidget(note)
        return card, layout

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    @classmethod
    def _add_labeled_row(cls, form: QFormLayout, text: str, control: QWidget) -> QLabel:
        label = cls._field_label(text)
        label.setBuddy(control)
        if not control.accessibleName():
            control.setAccessibleName(text)
        form.addRow(label, control)
        return label

    def _build_subtitles_page(self) -> QWidget:
        body, page = self._page_body()
        card, group = self._card("字幕内容", "德语始终显示在第一行；打开原文对照后，中文视频可同时看中德两行。")
        self.subtitle_combo = ChoiceGroup(columns=2)
        self.subtitle_combo.setAccessibleName("字幕内容")
        self.subtitle_combo.addItem("仅德语", False)
        self.subtitle_combo.addItem("德语 + 原文", True)
        self.subtitle_combo.setCurrentIndex(self.subtitle_combo.findData(self._settings.compare_original))
        group.addWidget(self.subtitle_combo)
        quick = QHBoxLayout()
        self.visibility_button = QPushButton("隐藏字幕")
        self.visibility_button.clicked.connect(self.visibility_requested.emit)
        self.pause_button = QPushButton("暂停识别")
        self.pause_button.clicked.connect(self.pause_requested.emit)
        quick.addWidget(self.visibility_button)
        quick.addWidget(self.pause_button)
        group.addLayout(quick)
        page.addWidget(card)

        card, group = self._card("声音来源", "选择正在播放游戏或视频声音的设备。")
        self.device_combo = QComboBox()
        self.device_combo.setAccessibleName("播放设备")
        self._set_devices(self._devices, self._settings.output_device_id)
        refresh_button = QPushButton("刷新设备")
        refresh_button.clicked.connect(self.refresh_devices_requested.emit)
        row = QHBoxLayout()
        row.addWidget(self.device_combo, 1)
        row.addWidget(refresh_button)
        group.addLayout(row)
        self.audio_level_label = QLabel("等待声音采集")
        self.audio_level_label.setObjectName("hint")
        self.audio_level_label.setWordWrap(True)
        group.addWidget(self.audio_level_label)
        language_label = self._field_label("声音语言")
        self.language_combo = ChoiceGroup(columns=4)
        self.language_combo.setAccessibleName("声音语言")
        for label, code in (("自动识别", "auto"), ("德语", "de"), ("英语", "en"), ("中文", "zh")):
            self.language_combo.addItem(label, code)
        self.language_combo.setCurrentIndex(self.language_combo.findData(self._settings.language_lock))
        language_label.setBuddy(self.language_combo.button_for_data("auto"))
        group.addWidget(language_label)
        group.addWidget(self.language_combo)
        page.addWidget(card)

        card, group = self._card("字幕位置", "选一个大致位置，或解锁字幕条直接拖动。")
        self.position_combo = ChoiceGroup(columns=3)
        self.position_combo.setAccessibleName("字幕位置")
        for label, code in (
            ("左上", "top-left"), ("上方", "top-center"), ("右上", "top-right"),
            ("左侧", "middle-left"), ("中央", "middle-center"), ("右侧", "middle-right"),
            ("左下", "bottom-left"), ("下方", "bottom-center"), ("右下", "bottom-right"),
        ):
            self.position_combo.addItem(label, code)
        self.position_combo.addItem("自定义拖动", "custom", span=3)
        self.position_combo.setCurrentIndex(self.position_combo.findData(self._settings.overlay_position))
        self.position_combo.button_for_data("custom").hide()
        group.addWidget(self.position_combo)
        self.custom_position_note = QLabel("当前使用自定义位置；选择上方九宫格可恢复预设位置。")
        self.custom_position_note.setObjectName("hint")
        self.custom_position_note.setVisible(self._settings.overlay_position == "custom")
        group.addWidget(self.custom_position_note)
        position_actions = QHBoxLayout()
        unlock_button = QPushButton("解锁并拖动")
        unlock_button.clicked.connect(self.unlock_requested.emit)
        reset_button = QPushButton("恢复下方位置")
        reset_button.clicked.connect(self.reset_position_requested.emit)
        position_actions.addWidget(unlock_button)
        position_actions.addWidget(reset_button)
        group.addLayout(position_actions)
        page.addWidget(card)
        page.addStretch(1)
        return body

    def _build_appearance_page(self) -> QWidget:
        body, page = self._page_body()
        card, group = self._card("字幕风格", "先选一个样式，再按需要微调颜色和尺寸。")
        self.style_combo = ChoiceGroup(columns=3)
        self.style_combo.setAccessibleName("字幕风格")
        for label, code in (("深色", "dark"), ("浅色", "light"),
                            ("透明文字", "clear"), ("描边", "outlined"), ("自定义", "custom")):
            self.style_combo.addItem(label, code)
        group.addWidget(self.style_combo)
        page.addWidget(card)

        card, group = self._card("尺寸与显示")
        form = QFormLayout()
        form.setSpacing(10)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(240, 3840)
        self.width_spin.setSuffix(" px")
        self.width_spin.setValue(self._settings.overlay_width)
        self._add_labeled_row(form, "字幕宽度", self.width_spin)
        self.font_spin = QSpinBox()
        self.font_spin.setRange(12, 72)
        self.font_spin.setSuffix(" pt")
        self.font_spin.setValue(self._settings.font_size)
        self._add_labeled_row(form, "德语字号", self.font_spin)
        self.fade_spin = QDoubleSpinBox()
        self.fade_spin.setRange(0, 30)
        self.fade_spin.setSuffix(" 秒")
        self.fade_spin.setValue(self._settings.fade_seconds)
        self._add_labeled_row(form, "无语音后隐藏", self.fade_spin)
        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.setDecimals(2)
        self.opacity_spin.setValue(self._settings.opacity)
        self._add_labeled_row(form, "背景不透明度", self.opacity_spin)
        group.addLayout(form)
        page.addWidget(card)

        card, group = self._card("颜色与边框")
        form = QFormLayout()
        form.setSpacing(10)
        self.background_button = self._color_button(self._settings.background_color, "背景")
        self.primary_button = self._color_button(self._settings.primary_color, "德语文字")
        self.secondary_button = self._color_button(self._settings.secondary_color, "原文")
        self.border_button = self._color_button(self._settings.border_color, "边框")
        for label, button in (("背景颜色", self.background_button), ("德语文字", self.primary_button),
                              ("原文颜色", self.secondary_button), ("边框颜色", self.border_button)):
            self._add_labeled_row(form, label, button)
        self.border_spin = QSpinBox()
        self.border_spin.setRange(0, 8)
        self.border_spin.setValue(self._settings.border_width)
        self._add_labeled_row(form, "边框宽度", self.border_spin)
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(0, 32)
        self.radius_spin.setValue(self._settings.corner_radius)
        self._add_labeled_row(form, "圆角半径", self.radius_spin)
        self.padding_x_spin = QSpinBox()
        self.padding_x_spin.setRange(0, 40)
        self.padding_x_spin.setValue(self._settings.padding_horizontal)
        self._add_labeled_row(form, "左右留白", self.padding_x_spin)
        self.padding_y_spin = QSpinBox()
        self.padding_y_spin.setRange(0, 24)
        self.padding_y_spin.setValue(self._settings.padding_vertical)
        self._add_labeled_row(form, "上下留白", self.padding_y_spin)
        group.addLayout(form)
        page.addWidget(card)
        page.addStretch(1)
        self.style_combo.currentIndexChanged.connect(self._apply_preset)
        for spin in (self.opacity_spin, self.border_spin, self.radius_spin,
                     self.padding_x_spin, self.padding_y_spin):
            spin.valueChanged.connect(self._mark_custom)
        self._select_matching_preset(self._settings)
        return body

    def _build_online_page(self) -> QWidget:
        body, page = self._page_body()
        card, group = self._card("处理方式", "本地模式无需上传电脑声音。在线模式须本次运行手动开启，可能产生费用。")
        self.mode_combo = ChoiceGroup(columns=2)
        self.mode_combo.setAccessibleName("处理方式")
        self.mode_combo.addItem("本地 · 默认", "local")
        self.mode_combo.addItem("在线 · Azure", "online")
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(self._settings.mode))
        group.addWidget(self.mode_combo)
        self.speed_hint = QLabel("在线模式固定声音语言通常能更早显示临时译文；自动识别多语言一般需要等待整句。")
        self.speed_hint.setObjectName("hint")
        self.speed_hint.setWordWrap(True)
        group.addWidget(self.speed_hint)
        page.addWidget(card)

        card, group = self._card("每日在线用量", "此上限只约束本应用发送的音频分钟数；请同时在 Azure 设置账单预算。")
        self.online_limit_spin = QSpinBox()
        self.online_limit_spin.setAccessibleName("每日在线用量")
        self.online_limit_spin.setRange(1, 1440)
        self.online_limit_spin.setSuffix(" 分钟")
        self.online_limit_spin.setValue(self._settings.online_minutes_limit)
        group.addWidget(self.online_limit_spin)
        page.addWidget(card)

        card, group = self._card("Azure 连接", "凭据只存入 Windows 凭据管理器，不写入设置文件。")
        form = QFormLayout()
        form.setSpacing(11)
        self.region_input = QLineEdit()
        self.region_input.setPlaceholderText("例如 eastasia")
        self.region_label = self._add_labeled_row(form, "Azure 区域", self.region_input)
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("粘贴 Azure Speech 密钥")
        self.key_label = self._add_labeled_row(form, "Azure 密钥", self.key_input)
        group.addLayout(form)
        save_key_button = QPushButton("保存在线凭据")
        save_key_button.clicked.connect(self._submit_credentials)
        group.addWidget(save_key_button)
        page.addWidget(card)
        page.addStretch(1)
        return body

    def _show_page(self, page: str) -> None:
        pages = {
            "subtitles": (0, "字幕", "选好声音和字幕，开始听德语。"),
            "appearance": (1, "外观", "让字幕条适合你正在看的画面。"),
            "online": (2, "在线服务", "需要更快的在线译文时，在这里管理模式与用量。"),
        }
        index, title, description = pages[page]
        self.pages.setCurrentIndex(index)
        self.page_title.setText(title)
        self.page_description.setText(description)
        for name, button in (("subtitles", self.nav_subtitles),
                             ("appearance", self.nav_appearance), ("online", self.nav_online)):
            button.setChecked(name == page)

    def current_page(self) -> str:
        return ("subtitles", "appearance", "online")[self.pages.currentIndex()]

    def set_runtime_controls(self, *, visible: bool, paused: bool) -> None:
        self.visibility_button.setText("隐藏字幕" if visible else "显示字幕")
        self.pause_button.setText("继续识别" if paused else "暂停识别")

    def has_pending_changes(self) -> bool:
        return self._candidate_settings() != self._settings

    @staticmethod
    def _set_button_color(button: QPushButton, color: str) -> None:
        button.setProperty("color", color)
        button.setText(color)
        name = button.property("color_name")
        if name:
            button.setAccessibleName(f"{name}颜色：{color}")
        value = QColor(color)
        foreground = "#111111" if value.lightness() > 150 else "#FFFFFF"
        button.setStyleSheet(f"background-color: {color}; color: {foreground}; border-radius: 8px;")

    def _color_button(self, color: str, name: str) -> QPushButton:
        button = QPushButton()
        button.setProperty("color_name", name)
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
        self.settings_changed.emit(self._candidate_settings())

    def _apply_and_preview(self) -> None:
        self.preview_requested.emit(self._candidate_settings())

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
        elif level < 0.0001:
            self.audio_level_label.setText("未检测到电脑播放声；请检查播放设备、Windows 音量混合器中的应用输出和系统音量")
        else:
            self.audio_level_label.setText("正在接收电脑播放声")

    def update_settings(self, settings: Settings) -> None:
        pending = self._candidate_settings()
        display = replace(settings, **{
            name: getattr(pending, name) for name in EDITABLE_FIELDS
            if getattr(pending, name) != getattr(self._settings, name)
        })
        self._settings = settings
        self._set_devices(self._devices, display.output_device_id)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(display.mode))
        self.language_combo.setCurrentIndex(self.language_combo.findData(display.language_lock))
        self.subtitle_combo.setCurrentIndex(self.subtitle_combo.findData(display.compare_original))
        self.position_combo.setCurrentIndex(self.position_combo.findData(display.overlay_position))
        self.custom_position_note.setVisible(display.overlay_position == "custom")
        self.width_spin.setValue(display.overlay_width)
        self.font_spin.setValue(display.font_size)
        self.opacity_spin.setValue(display.opacity)
        for name, button in (("background_color", self.background_button),
                             ("primary_color", self.primary_button),
                             ("secondary_color", self.secondary_button),
                             ("border_color", self.border_button)):
            self._set_button_color(button, getattr(display, name))
        self.border_spin.setValue(display.border_width)
        self.radius_spin.setValue(display.corner_radius)
        self.padding_x_spin.setValue(display.padding_horizontal)
        self.padding_y_spin.setValue(display.padding_vertical)
        self._select_matching_preset(display)
        self.fade_spin.setValue(display.fade_seconds)
        self.online_limit_spin.setValue(display.online_minutes_limit)

    def replace_devices(self, devices: list[OutputDevice]) -> None:
        selected = self.device_combo.currentData()
        self._devices = list(devices)
        self._set_devices(self._devices, selected)

    def _set_devices(self, devices: list[OutputDevice], selected: str | None) -> None:
        self.device_combo.clear()
        self.device_combo.addItem("系统默认播放设备", None)
        for device in devices:
            self.device_combo.addItem(device.name, device.id)
        index = self.device_combo.findData(selected)
        if index < 0 and selected is not None:
            self.device_combo.addItem("已选播放设备（暂不可用，等待重新连接）", selected)
            index = self.device_combo.count() - 1
            self.device_combo.setItemData(index, selected, 3)
        self.device_combo.setCurrentIndex(max(0, index))

    def closeEvent(self, event) -> None:
        self.history_dialog.hide()
        if self.close_exits:
            event.ignore()
            self.exit_requested.emit()
            return
        super().closeEvent(event)
