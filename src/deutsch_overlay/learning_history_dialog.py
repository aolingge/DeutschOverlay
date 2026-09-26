"""Small review panel for captions captured during the current app run."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from deutsch_overlay.ui_theme import HISTORY_STYLE


class LearningHistoryDialog(QDialog):
    clear_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("historyDialog")
        self.setStyleSheet(HISTORY_STYLE)
        self.setWindowTitle("德语学习记录 · 本次运行")
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(600, 460)
        else:
            available = screen.availableGeometry()
            width = min(600, max(1, available.width() - 40))
            height = min(460, max(1, available.height() - 60))
            self.resize(width, height)
            self.move(available.left() + (available.width() - width) // 2,
                      available.top() + (available.height() - height) // 2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 21, 22, 20)
        layout.setSpacing(12)
        title = QLabel("学习记录")
        title.setObjectName("historyTitle")
        layout.addWidget(title)
        description = QLabel("这里只保留本次运行已完成的字幕，最多 200 条；关闭应用后自动消失。")
        description.setObjectName("historyDescription")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("播放德语、英语或中文语音后，完成的字幕会出现在这里。")
        layout.addWidget(self.text)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.copy_button = QPushButton("复制全部")
        self.copy_button.setObjectName("primaryButton")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_all)
        self.clear_button = QPushButton("清空本次记录")
        self.clear_button.setEnabled(False)
        self.clear_button.clicked.connect(self.clear_requested.emit)
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.copy_button)
        layout.addLayout(buttons)

    def set_history(self, value: str) -> None:
        scroll = self.text.verticalScrollBar()
        follow_latest = scroll.value() >= scroll.maximum() - 1
        self.text.setPlainText(value)
        has_entries = bool(value.strip())
        self.copy_button.setEnabled(has_entries)
        self.clear_button.setEnabled(has_entries)
        if follow_latest:
            scroll.setValue(scroll.maximum())

    def _copy_all(self) -> None:
        content = self.text.toPlainText()
        if content:
            QGuiApplication.clipboard().setText(content)
