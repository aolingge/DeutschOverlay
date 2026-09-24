"""Small transparent caption strip that can sit above borderless games."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from deutsch_overlay.captions import CaptionView
from deutsch_overlay.config import Settings


class CaptionOverlay(QWidget):
    moved = Signal(int, int)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._locked = True
        self._drag_origin: QPoint | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._apply_flags()

        self.frame = QFrame(self)
        self.primary_label = QLabel(self.frame)
        self.primary_label.setWordWrap(True)
        self.primary_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.secondary_label = QLabel(self.frame)
        self.secondary_label.setWordWrap(True)
        self.secondary_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.secondary_label.hide()

        inner = QVBoxLayout(self.frame)
        inner.setContentsMargins(18, 9, 18, 9)
        inner.setSpacing(2)
        inner.addWidget(self.primary_label)
        inner.addWidget(self.secondary_label)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.frame)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_caption)
        self.set_style(settings)

    def _apply_flags(self) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self._locked:
            flags |= Qt.WindowType.WindowTransparentForInput
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()

    def set_locked(self, locked: bool) -> None:
        if self._locked != locked:
            self._locked = locked
            self._apply_flags()

    def set_style(self, settings: Settings) -> None:
        self._settings = settings
        primary_font = self.primary_label.font()
        primary_font.setPointSize(settings.font_size)
        primary_font.setBold(True)
        self.primary_label.setFont(primary_font)
        secondary_font = self.secondary_label.font()
        secondary_font.setPointSize(max(12, settings.font_size - 8))
        self.secondary_label.setFont(secondary_font)
        alpha = round(settings.opacity * 220)
        self.frame.setStyleSheet(
            f"QFrame {{ background-color: rgba(0, 0, 0, {alpha}); border-radius: 8px; }}"
            "QLabel { color: white; background: transparent; }"
        )
        self.primary_label.setStyleSheet("color: white; background: transparent;")
        self.secondary_label.setStyleSheet("color: #dddddd; background: transparent;")
        self.setFixedWidth(settings.overlay_width)
        self._fit_text()

    def _fit_text(self) -> None:
        text_width = max(1, self.width() - 36)
        primary_height = max(self.primary_label.fontMetrics().height(), self.primary_label.heightForWidth(text_width))
        self.primary_label.setFixedHeight(primary_height)
        secondary_height = 0
        if not self.secondary_label.isHidden():
            secondary_height = max(self.secondary_label.fontMetrics().height(), self.secondary_label.heightForWidth(text_width))
            self.secondary_label.setFixedHeight(secondary_height)
        self.setFixedHeight(18 + primary_height + (2 + secondary_height if secondary_height else 0))
        self.layout().activate()
        self._place_on_screen()

    def _place_on_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        x, y = self._settings.overlay_x, self._settings.overlay_y
        target = next(
            (candidate for candidate in QGuiApplication.screens()
             if x is not None and y is not None and candidate.availableGeometry().contains(QPoint(x, y))),
            None,
        )
        if target is None:
            target = screen
        rect = target.availableGeometry()
        if x is None or y is None or target is screen and not rect.contains(QPoint(x, y)):
            x = rect.center().x() - self.width() // 2
            y = rect.bottom() - self.height() - 55
        x = max(rect.left(), min(x, rect.right() - self.width() + 1))
        y = max(rect.top(), min(y, rect.bottom() - self.height() + 1))
        self.move(x, y)

    def show_caption(self, view: CaptionView) -> None:
        self.primary_label.setText(view.primary)
        self.secondary_label.setText(view.secondary or "")
        self.secondary_label.setVisible(bool(view.secondary))
        self._fit_text()
        self.show()
        self.raise_()
        self._hide_timer.stop()
        if view.final:
            self._hide_timer.start(max(1, round(self._settings.fade_seconds * 1000)))

    def hide_caption(self) -> None:
        self._hide_timer.stop()
        self.hide()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._locked and event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None:
            self._drag_origin = None
            self.moved.emit(self.x(), self.y())
            event.accept()
