"""Pure caption routing and view state for the overlay."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CaptionEvent:
    session_id: int
    segment_id: str
    language: str
    original: str
    german: str | None
    final: bool
    timestamp: float
    device_epoch: int = 0


@dataclass(frozen=True, slots=True)
class CaptionView:
    session_id: int
    segment_id: str
    primary: str
    secondary: str | None
    final: bool
    timestamp: float


def _two_lines(value: str) -> str:
    lines = [line.strip() for line in value.replace("\r", "").split("\n") if line.strip()]
    if len(lines) <= 2:
        return "\n".join(lines)
    return f"{lines[0]}\n{lines[1]}…"


class CaptionReducer:
    """Accept current-session events and expose immutable overlay text."""

    def __init__(self, session_id: int, compare_original: bool = False) -> None:
        self.session_id = session_id
        self.compare_original = compare_original
        self.current: CaptionView | None = None
        self._last_event: CaptionEvent | None = None

    def reset(self, session_id: int) -> None:
        self.session_id = session_id
        self.current = None
        self._last_event = None

    def set_compare_original(self, enabled: bool) -> CaptionView | None:
        self.compare_original = enabled
        if self._last_event is None:
            return None
        view = self._render(self._last_event)
        self.current = view
        return view

    def apply(self, event: CaptionEvent) -> CaptionView | None:
        if event.session_id != self.session_id or event == self._last_event:
            return None
        if self._last_event is not None and event.timestamp < self._last_event.timestamp:
            return None
        view = self._render(event)
        if view is None:
            return None
        self._last_event = event
        self.current = view
        return view

    def _render(self, event: CaptionEvent) -> CaptionView | None:
        if event.language not in {"de", "en", "zh"}:
            return None
        primary = _two_lines(event.original if event.language == "de" else event.german or "")
        if not primary:
            return None
        secondary = None
        if self.compare_original and event.language != "de":
            secondary = _two_lines(event.original) or None
        return CaptionView(
            session_id=event.session_id,
            segment_id=event.segment_id,
            primary=primary,
            secondary=secondary,
            final=event.final,
            timestamp=event.timestamp,
        )
