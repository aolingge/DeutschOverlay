"""Keep reported omissions and timestamp coverage honest."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("faster_whisper")
spec = importlib.util.spec_from_file_location("benchmark_recognition", Path(__file__).parents[1] / "scripts" / "benchmark-recognition.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_omission_and_insertion_are_not_conflated():
    assert benchmark.edit_counts(["one", "two", "three"], ["one", "three"]) == {
        "total": 1, "substitutions": 0, "deletions": 1, "insertions": 0}
    assert benchmark.edit_counts([], ["hallucination"])["insertions"] == 1
    assert benchmark.edit_counts(["omitted"], [])["deletions"] == 1


@pytest.mark.parametrize("reference,hypothesis", [("aba", "aca"), ("abc", "cba"), ("", "ab"), ("ab", "")])
def test_alignment_counts_match_minimum_distance(reference, hypothesis):
    counts = benchmark.edit_counts(list(reference), list(hypothesis))
    assert counts["total"] == benchmark.distance(list(reference), list(hypothesis))
    assert counts["total"] == sum(counts[key] for key in ("substitutions", "deletions", "insertions"))


def test_timing_coverage_rejects_zero_duration_and_missing_words():
    segment = SimpleNamespace(text="eins zwei drei", words=[
        SimpleNamespace(text="eins", start_ms=0, end_ms=100),
        SimpleNamespace(text="zwei", start_ms=100, end_ms=100)])
    result = benchmark.timing_coverage([segment], "de")
    assert result == {"text_units": 3, "timed_units": 1, "invalid_words": 1, "coverage": 1 / 3}
    assert benchmark.timing_coverage([], "en")["coverage"] is None


def test_timing_coverage_supports_whisper_seconds_and_chinese_units():
    segment = SimpleNamespace(text="中文", words=[SimpleNamespace(word="中文", start=0.0, end=0.5)])
    assert benchmark.timing_coverage([segment], "zh")["coverage"] == 1
