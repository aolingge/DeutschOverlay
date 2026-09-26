"""Small review panel for captions captured during the current app run."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout


class LearningHistoryDialog(QDialog):
    clear_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("德语学习记录 · 本次运行")
        self.resize(600, 460)
        layout = QVBoxLayout(self)
        description = QLabel("这里只保留本次运行已完成的字幕，最多 200 条；关闭应用后自动消失。")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("播放德语、英语或中文语音后，完成的字幕会出现在这里。")
        layout.addWidget(self.text)
        buttons = QHBoxLayout()
        self.copy_button = QPushButton("复制全部")
        self.copy_button.clicked.connect(self._copy_all)
        self.clear_button = QPushButton("清空本次记录")
        self.clear_button.clicked.connect(self.clear_requested.emit)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.clear_button)
        layout.addLayout(buttons)

    def set_history(self, value: str) -> None:
        scroll = self.text.verticalScrollBar()
        follow_latest = scroll.value() >= scroll.maximum() - 1
        self.text.setPlainText(value)
        if follow_latest:
            scroll.setValue(scroll.maximum())

    def _copy_all(self) -> None:
        QGuiApplication.clipboard().setText(self.text.toPlainText())
