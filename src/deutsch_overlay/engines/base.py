"""Shared caption-engine contract."""

from __future__ import annotations

from typing import Protocol

import numpy as np

from deutsch_overlay.captions import CaptionEvent


class CaptionEngine(Protocol):
    def process(
        self,
        audio: np.ndarray,
        sample_rate: int,
        session_id: int,
        segment_id: str,
        language_lock: str | None,
    ) -> CaptionEvent | None: ...
