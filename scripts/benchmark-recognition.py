"""Silent, local before/after decoding against an explicit audio/reference manifest.

No download, playback, capture, token or service. WER (whitespace words) and
Chinese CER use punctuation-free Unicode casefolded references. This benchmark
is evidence for the supplied fixtures only, not a general accuracy guarantee.
"""
import argparse
import hashlib
import importlib.util
import json
import time
import unicodedata
from pathlib import Path

import av
import numpy as np
from faster_whisper import WhisperModel

from deutsch_overlay.models import ModelStore
from deutsch_overlay.pcm import Resampler
from deutsch_overlay.engines.local import LocalEngine
from deutsch_overlay.gpu_runtime import prepare_cuda_dlls


def units(text, language):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(c if unicodedata.category(c)[0] in "LN" else " " for c in text)
    return list("".join(text.split())) if language == "zh" else text.split()


def distance(reference, hypothesis):
    # Exact unit edit distance. No network dependency is needed for these
    # short, fixed fixtures; insertion/deletion/substitution all cost one.
    row = list(range(len(hypothesis) + 1))
    for i, a in enumerate(reference, 1):
        next_row = [i]
        for j, b in enumerate(hypothesis, 1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j - 1] + (a != b)))
        row = next_row
    return row[-1]


def edit_counts(reference, hypothesis):
    """Return an optimal edit alignment, preferring matches then substitutions.

    Deletions measure omitted reference units, not verified missing sentences.
    Several equally optimal alignments may exist; the tie rule is deterministic.
    """
    row = [(j, 0, 0, j) for j in range(len(hypothesis) + 1)]
    for i, token in enumerate(reference, 1):
        new = [(i, 0, i, 0)]
        for j, candidate in enumerate(hypothesis, 1):
            diag, delete, insert = row[j - 1], row[j], new[-1]
            cost = int(token != candidate)
            choices = [(diag[0] + cost, diag[1] + cost, diag[2], diag[3]),
                       (delete[0] + 1, delete[1], delete[2] + 1, delete[3]),
                       (insert[0] + 1, insert[1], insert[2], insert[3] + 1)]
            new.append(min(choices, key=lambda x: x[0]))
        row = new
    total, substitutions, deletions, insertions = row[-1]
    return dict(total=total, substitutions=substitutions, deletions=deletions, insertions=insertions)


def timing_coverage(decoded, language):
    """Text coverage by valid word timestamps, not human-aligned accuracy."""
    text_units, timed_units, invalid = 0, 0, 0
    for segment in decoded:
        text_units += len(units(segment.text, language))
        for word in segment.words or []:
            start = getattr(word, "start", getattr(word, "start_ms", None))
            end = getattr(word, "end", getattr(word, "end_ms", None))
            value = getattr(word, "word", getattr(word, "text", ""))
            if start is None or end is None or not np.isfinite([start, end]).all() or start < 0 or end <= start:
                invalid += 1
            else:
                timed_units += len(units(value, language))
    return dict(text_units=text_units, timed_units=timed_units, invalid_words=invalid,
                coverage=min(1.0, timed_units / text_units) if text_units else None)


def load_audio(path, rate):
    chunks = []
    converter = av.AudioResampler(format="fltp", layout="mono", rate=rate)
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            chunks.extend(x.to_ndarray().reshape(-1) for x in converter.resample(frame))
    chunks.extend(x.to_ndarray().reshape(-1) for x in converter.resample(None))
    return np.concatenate(chunks).astype(np.float32)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--baseline-pcm", type=Path, help="Optional historical resampler for a before/after comparison")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--after-beam", type=int, choices=(3, 5), default=3)
    parser.add_argument("--model-path", type=Path, help="Existing local CTranslate2 Whisper model; never downloaded")
    parser.add_argument("--chinese-script", choices=("raw", "simplified", "traditional"), default="raw")
    parser.add_argument("--hotwords-file", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--input-rates", type=int, nargs="+", default=[44100, 48000], choices=(16000, 44100, 48000))
    args = parser.parse_args()
    baseline = None
    if args.baseline_pcm:
        spec = importlib.util.spec_from_file_location("deutsch_overlay.baseline_pcm", args.baseline_pcm)
        baseline = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = baseline
        spec.loader.exec_module(baseline)
    terms = json.loads(args.hotwords_file.read_text(encoding="utf-8-sig")) if args.hotwords_file else {}
    engine = LocalEngine(asr_model_path=args.model_path, chinese_script=args.chinese_script, hotwords=terms)
    model_path = engine.asr_model_path or ModelStore().require("whisper-small")
    if args.device == "cuda":
        prepare_cuda_dlls()
    compute_type = "int8_float16" if args.device == "cuda" else "int8"
    model = WhisperModel(str(model_path), device=args.device, compute_type=compute_type, cpu_threads=4)
    engine.asr = model
    report = {"model": str(model_path), "device": args.device, "compute_type": compute_type, "cases": [],
              "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
              "input_rates": args.input_rates,
              "baseline_sha256": hashlib.sha256(args.baseline_pcm.read_bytes()).hexdigest() if args.baseline_pcm else None,
              "chinese_script": args.chinese_script,
              "hotwords_enabled": bool(terms),
              "current_sha256": hashlib.sha256(Path(__import__('deutsch_overlay.pcm', fromlist=['']).__file__).read_bytes()).hexdigest(),
              "limitations": ["Supplied fixtures only; no claim of real-world WER.",
                               "Optional historical pipeline comparison changes beam and resampling together; those effects are not isolated.",
                               "Whole-file decoding; no live capture, bridge segmentation or end-to-end latency measurement.",
                               "Timing confidence is not validated against human word annotations."]}
    for case in json.loads(args.manifest.read_text(encoding="utf-8-sig"))["cases"]:
        for rate in args.input_rates:
            audio = load_audio(case["audio"], rate)
            pipelines = [("after", Resampler, args.after_beam, True)]
            if baseline:
                pipelines.insert(0, ("before", baseline.Resampler, 3, False))
            for name, cls, beam, words in pipelines:
                converter = cls(rate)
                chunks = [converter.process(audio[i:i+rate//10]) for i in range(0, len(audio), rate//10)]
                chunks.append(converter.flush())
                signal = np.concatenate(chunks)
                start = time.perf_counter()
                if name == "after":
                    result = engine.transcribe_segments(signal, language=case["language"], beam_size=beam,
                                                        word_timestamps=words, offset_samples=0)
                    decoded = result.segments
                    text = result.text
                    raw_text = " ".join(s.raw_text or s.text for s in decoded)
                else:
                    segments, info = model.transcribe(signal, language=case["language"], beam_size=beam,
                        condition_on_previous_text=False, word_timestamps=words, vad_filter=True,
                        hallucination_silence_threshold=1.0 if words else None)
                    decoded = list(segments)
                    text = raw_text = " ".join(s.text.strip() for s in decoded)
                ref, hyp = units(case["reference"], case["language"]), units(text, case["language"])
                equivalent_ref, equivalent_hyp = ref, hyp
                if case["language"] == "zh" and args.chinese_script != "raw":
                    equivalent_ref = units(engine._normalize_chinese(case["reference"]), "zh")
                    equivalent_hyp = units(engine._normalize_chinese(raw_text), "zh")
                item = {"id": case["id"], "kind": case.get("kind"), "language": case["language"],
                        "corpus_language": case.get("corpus_language", case["language"]),
                        "source_id": case.get("source_id", case["id"]),
                        "speaker_id": case.get("speaker_id"),
                        "input_rate": rate, "pipeline": name, "beam": beam,
                        "word_timestamps": words, "reference": case["reference"], "hypothesis": text,
                        "raw_hypothesis": raw_text,
                        "raw_edit_distance": distance(ref, units(raw_text, case["language"])),
                        "script_equivalent_edit_distance": distance(equivalent_ref, equivalent_hyp),
                        "script_equivalent_reference_units": len(equivalent_ref),
                        "metric": "CER" if case["language"] == "zh" else "WER",
                        "edit_distance": distance(ref, hyp), "reference_units": len(ref),
                        "error_rate": distance(ref, hyp) / max(1, len(ref)),
                        "seconds": time.perf_counter()-start,
                        "duration": len(signal)/16000, "words": sum(len(s.words or []) for s in decoded),
                        "audio_sha256": hashlib.sha256(Path(case["audio"]).read_bytes()).hexdigest()}
                item["edits"] = edit_counts(equivalent_ref, equivalent_hyp)
                item["empty_hypothesis"] = bool(equivalent_ref) and not equivalent_hyp
                item["timing"] = timing_coverage(decoded, case["language"])
                report["cases"].append(item)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(case["id"], rate, name, item["metric"], round(item["error_rate"], 4), flush=True)


if __name__ == "__main__":
    main()
