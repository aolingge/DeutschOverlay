from deutsch_overlay.captions import CaptionEvent, CaptionReducer


def event(language="de", original="Guten Tag", german=None, *, session=1, segment="a", final=True):
    return CaptionEvent(session, segment, language, original, german, final, 1.0)


def test_german_source_is_shown_once_even_in_comparison_mode():
    reducer = CaptionReducer(session_id=1, compare_original=True)
    view = reducer.apply(event())
    assert view.primary == "Guten Tag"
    assert view.secondary is None


def test_english_and_chinese_need_german_translation():
    reducer = CaptionReducer(session_id=1)
    assert reducer.apply(event("en", "Hello", None)) is None
    english = reducer.apply(event("en", "Hello", "Hallo"))
    assert english.primary == "Hallo"
    assert english.secondary is None
    chinese = reducer.apply(event("zh", "你好", "Hallo", segment="b"))
    assert chinese.primary == "Hallo"


def test_comparison_toggles_without_new_engine_result():
    reducer = CaptionReducer(session_id=1)
    reducer.apply(event("en", "Hello", "Hallo"))
    assert reducer.set_compare_original(True).secondary == "Hello"
    assert reducer.set_compare_original(False).secondary is None


def test_same_segment_updates_one_caption_and_ignores_repeated_event():
    reducer = CaptionReducer(session_id=1)
    first = reducer.apply(event(original="Gute", final=False))
    assert first.final is False
    assert reducer.apply(event(original="Gute", final=False)) is None
    final = reducer.apply(event(original="Guten Tag", final=True))
    assert final.primary == "Guten Tag"
    assert final.final is True


def test_stale_session_is_rejected_after_reset():
    reducer = CaptionReducer(session_id=1)
    reducer.apply(event())
    reducer.reset(2)
    assert reducer.apply(event(session=1)) is None
    assert reducer.current is None
    assert reducer.apply(event(session=2, original="Weiter")).primary == "Weiter"


def test_caption_text_is_limited_to_two_lines():
    reducer = CaptionReducer(session_id=1)
    view = reducer.apply(event(original="Eins\nZwei\nDrei"))
    assert view.primary == "Eins\nZwei…"


def test_unsupported_language_does_not_show_wrong_caption():
    reducer = CaptionReducer(session_id=1)
    assert reducer.apply(event("fr", "Bonjour", "Hallo")) is None
