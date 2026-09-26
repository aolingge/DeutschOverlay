import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deutsch_overlay.captions import CaptionView
from deutsch_overlay.learning_history import CaptionHistory


def caption(segment: str, *, final: bool = True, timestamp: float = 1.0,
            original: str | None = "你好") -> CaptionView:
    return CaptionView(1, segment, "Guten Tag", None, final, timestamp,
                       source_original=original)


def test_history_only_keeps_final_lines_and_merges_same_event():
    history = CaptionHistory(limit=3)
    assert not history.add(caption("one", final=False))
    assert history.add(caption("one"))
    assert not history.add(caption("one"))
    assert len(history.entries) == 1
    assert history.entries[0].original == "你好"
    assert "Guten Tag\n你好" in history.text()


def test_history_bounds_memory_but_accepts_reused_segment_id_with_new_time():
    history = CaptionHistory(limit=2)
    for index in range(3):
        assert history.add(caption("online-1", timestamp=float(index + 1)))
    assert len(history.entries) == 2
    assert [entry.timestamp for entry in history.entries] == [2.0, 3.0]
    history.clear()
    assert history.entries == ()
    assert history.text() == ""
