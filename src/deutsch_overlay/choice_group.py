"""A labelled, keyboard-accessible group for a small number of choices."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QPushButton, QWidget


class ChoiceGroup(QWidget):
    currentIndexChanged = Signal(int)

    def __init__(self, *, columns: int = 4, parent=None) -> None:
        super().__init__(parent)
        self._buttons: list[QPushButton] = []
        self._values: list[object] = []
        self._current_index = -1
        self._columns = columns
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(7)
        self._layout.setVerticalSpacing(7)

    def addItem(self, label: str, value: object, *, span: int = 1) -> None:
        index = len(self._buttons)
        button = QPushButton(label, self)
        button.setObjectName("choiceButton")
        button.setCheckable(True)
        button.setMinimumHeight(38)
        button.clicked.connect(lambda _checked=False, selected=index: self.setCurrentIndex(selected))
        self._buttons.append(button)
        self._values.append(value)
        self._layout.addWidget(button, index // self._columns, index % self._columns, 1, span)
        if index == 0:
            self.setCurrentIndex(0)

    def findData(self, value: object) -> int:
        try:
            return self._values.index(value)
        except ValueError:
            return -1

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < len(self._buttons) or index == self._current_index:
            return
        self._current_index = index
        for number, button in enumerate(self._buttons):
            button.setChecked(number == index)
        self.currentIndexChanged.emit(index)

    def currentData(self):
        return self._values[self._current_index] if self._current_index >= 0 else None

    def currentIndex(self) -> int:
        return self._current_index

    def count(self) -> int:
        return len(self._buttons)

    def button_for_data(self, value: object) -> QPushButton | None:
        index = self.findData(value)
        return self._buttons[index] if index >= 0 else None
