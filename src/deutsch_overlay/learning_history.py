"""Bounded, in-memory review of finalized German captions."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from deutsch_overlay.captions import CaptionView


@dataclass(frozen=True, slots=True)
class HistoryLine:
    session_id: int
    segment_id: str
    timestamp: float
    german: str
    original: str | None


class CaptionHistory:
    def __init__(self, limit: int = 200) -> None:
        if limit < 1:
            raise ValueError("history limit must be positive")
        self._entries: deque[HistoryLine] = deque(maxlen=limit)

    @property
    def entries(self) -> tuple[HistoryLine, ...]:
        return tuple(self._entries)

    def add(self, view: CaptionView) -> bool:
        if not view.final or not view.primary.strip():
            return False
        previous = self._entries[-1] if self._entries else None
        same_event = previous is not None and (
            previous.session_id, previous.segment_id, previous.timestamp
        ) == (view.session_id, view.segment_id, view.timestamp)
        original = view.source_original or (previous.original if same_event else None)
        entry = HistoryLine(view.session_id, view.segment_id, view.timestamp,
                            view.full_primary or view.primary, original)
        if same_event:
            if entry == previous:
                return False
            self._entries[-1] = entry
        else:
            self._entries.append(entry)
        return True

    def clear(self) -> None:
        self._entries.clear()

    def text(self) -> str:
        return "\n\n".join(
            f"{index}. {line.german}" + (f"\n{line.original}" if line.original else "")
            for index, line in enumerate(self._entries, start=1)
        )
