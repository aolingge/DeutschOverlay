"""Small transparent caption strip that can sit above borderless games."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QLabel, QVBoxLayout, QWidget

from deutsch_overlay.captions import CaptionView
from deutsch_overlay.config import Settings


class CaptionOverlay(QWidget):
    moved = Signal(int, int)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._locked = True
        self._drag_origin: QPoint | None = None
        self._primary_text = ""
        self._secondary_text = ""
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._apply_flags()

        self.frame = QFrame(self)
        self.frame.setObjectName("captionFrame")
        self.primary_label = QLabel(self.frame)
        self.primary_opacity = QGraphicsOpacityEffect(self.primary_label)
        self.primary_label.setGraphicsEffect(self.primary_opacity)
        self.primary_label.setTextFormat(Qt.TextFormat.PlainText)
        self.primary_label.setWordWrap(True)
        self.primary_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.secondary_label = QLabel(self.frame)
        self.secondary_opacity = QGraphicsOpacityEffect(self.secondary_label)
        self.secondary_label.setGraphicsEffect(self.secondary_opacity)
        self.secondary_label.setTextFormat(Qt.TextFormat.PlainText)
        self.secondary_label.setWordWrap(True)
        self.secondary_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.secondary_label.hide()

        self.inner_layout = QVBoxLayout(self.frame)
        self.inner_layout.setContentsMargins(18, 9, 18, 9)
        self.inner_layout.setSpacing(2)
        self.inner_layout.addWidget(self.primary_label)
        self.inner_layout.addWidget(self.secondary_label)
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
        alpha = round(settings.opacity * 255)
        color = settings.background_color
        red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
        self.inner_layout.setContentsMargins(
            settings.padding_horizontal + settings.border_width,
            settings.padding_vertical + settings.border_width,
            settings.padding_horizontal + settings.border_width,
            settings.padding_vertical + settings.border_width,
        )
        self.frame.setStyleSheet(
            f"QFrame#captionFrame {{ background-color: rgba({red}, {green}, {blue}, {alpha}); "
            f"border: {settings.border_width}px solid {settings.border_color}; "
            f"border-radius: {settings.corner_radius}px; }}"
        )
        self.primary_label.setStyleSheet(f"color: {settings.primary_color}; background: transparent;")
        self.secondary_label.setStyleSheet(f"color: {settings.secondary_color}; background: transparent;")
        self._fit_text()

    def _target_screen(self):
        x, y = self._settings.overlay_x, self._settings.overlay_y
        if self._settings.overlay_position == "custom" and x is not None and y is not None:
            for screen in QGuiApplication.screens():
                if screen.availableGeometry().contains(QPoint(x, y)):
                    return screen
        return QGuiApplication.primaryScreen()

    def _set_font_size(self, size: int) -> None:
        primary_font = self.primary_label.font()
        primary_font.setPointSize(size)
        primary_font.setBold(True)
        self.primary_label.setFont(primary_font)
        secondary_font = self.secondary_label.font()
        secondary_font.setPointSize(max(12, size - 8))
        self.secondary_label.setFont(secondary_font)

    @staticmethod
    def _text_height(label: QLabel, width: int) -> int:
        return max(label.fontMetrics().height(), label.heightForWidth(width))

    def _elide_to_height(self, label: QLabel, source: str, width: int, limit: int) -> int:
        label.setText(source)
        if self._text_height(label, width) <= limit:
            return self._text_height(label, width)
        low, high = 0, len(source)
        best = "…"
        while low <= high:
            middle = (low + high) // 2
            candidate = source[:middle].rstrip() + "…"
            label.setText(candidate)
            if self._text_height(label, width) <= limit:
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        label.setText(best)
        return min(limit, self._text_height(label, width))

    def _fit_text(self) -> None:
        screen = self._target_screen()
        rect = screen.availableGeometry() if screen is not None else None
        display_width = min(self._settings.overlay_width, rect.width()) if rect is not None else self._settings.overlay_width
        self.setFixedWidth(display_width)
        max_height = min(rect.height(), max(120, round(rect.height() * 0.4))) if rect is not None else 10000
        margins = self.inner_layout.contentsMargins()
        frame_border = self.frame.frameWidth() * 2
        text_width = max(1, display_width - margins.left() - margins.right() - frame_border)
        has_secondary = bool(self._secondary_text)
        self.secondary_label.setVisible(has_secondary)
        spacing = self.inner_layout.spacing() if has_secondary else 0
        content_limit = max(1, max_height - margins.top() - margins.bottom() - spacing - frame_border)

        def measure(size: int) -> tuple[int, int]:
            self._set_font_size(size)
            self.primary_label.setText(self._primary_text)
            self.secondary_label.setText(self._secondary_text)
            primary = self._text_height(self.primary_label, text_width)
            secondary = self._text_height(self.secondary_label, text_width) if has_secondary else 0
            return primary, secondary

        low, high, best = 12, self._settings.font_size, 12
        while low <= high:
            size = (low + high) // 2
            primary_height, secondary_height = measure(size)
            if primary_height + secondary_height <= content_limit:
                best = size
                low = size + 1
            else:
                high = size - 1
        primary_height, secondary_height = measure(best)
        if primary_height + secondary_height > content_limit:
            secondary_reserve = min(secondary_height, max(
                self.secondary_label.fontMetrics().height(), content_limit // 3
            )) if has_secondary else 0
            primary_limit = max(1, min(primary_height, content_limit - secondary_reserve))
            secondary_limit = max(1, content_limit - primary_limit)
            primary_height = self._elide_to_height(self.primary_label, self._primary_text, text_width, primary_limit)
            if has_secondary:
                secondary_height = self._elide_to_height(
                    self.secondary_label, self._secondary_text, text_width, secondary_limit
                )
        self.primary_label.setFixedHeight(primary_height)
        if has_secondary:
            self.secondary_label.setFixedHeight(secondary_height)
        self.setFixedHeight(min(max_height, margins.top() + primary_height + spacing +
                                secondary_height + margins.bottom() + frame_border))
        self.layout().activate()
        self._place_on_screen()

    def _place_on_screen(self) -> None:
        target = self._target_screen()
        if target is None:
            return
        x, y = self._settings.overlay_x, self._settings.overlay_y
        custom = self._settings.overlay_position == "custom"
        rect = target.availableGeometry()
        if not custom or x is None or y is None or not rect.contains(QPoint(x, y)):
            vertical, horizontal = self._settings.overlay_position.split("-") if not custom else ("bottom", "center")
            x = {"left": rect.left() + 45, "center": rect.center().x() - self.width() // 2,
                 "right": rect.right() - self.width() - 44}[horizontal]
            y = {"top": rect.top() + 55, "middle": rect.center().y() - self.height() // 2,
                 "bottom": rect.bottom() - self.height() - 55}[vertical]
        x = max(rect.left(), min(x, rect.right() - self.width() + 1))
        y = max(rect.top(), min(y, rect.bottom() - self.height() + 1))
        self.move(x, y)

    def show_caption(self, view: CaptionView, *, timeout_seconds: float | None = None) -> None:
        self._primary_text = view.primary
        self._secondary_text = view.secondary or ""
        opacity = 1.0 if view.final else 0.78
        self.primary_opacity.setOpacity(opacity)
        self.secondary_opacity.setOpacity(opacity)
        self._fit_text()
        self.show()
        self.raise_()
        self._hide_timer.stop()
        timeout = (self._settings.fade_seconds if view.final else max(2.0, self._settings.fade_seconds))
        if timeout_seconds is not None:
            timeout = timeout_seconds
        self._hide_timer.start(max(1, round(timeout * 1000)))

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
