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
    parser.add_argument("--baseline-pcm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--after-beam", type=int, choices=(3, 5), default=3)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("deutsch_overlay.baseline_pcm", args.baseline_pcm)
    baseline = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    model_path = ModelStore().require("whisper-small")
    model = WhisperModel(str(model_path), device="cpu", compute_type="int8", cpu_threads=4)
    report = {"model": str(model_path), "device": "cpu", "compute_type": "int8", "cases": [],
              "baseline_sha256": hashlib.sha256(args.baseline_pcm.read_bytes()).hexdigest(),
              "current_sha256": hashlib.sha256(Path(__import__('deutsch_overlay.pcm', fromlist=['']).__file__).read_bytes()).hexdigest(),
              "limitations": ["Supplied fixtures only; no claim of real-world WER.",
                               "Combined pipeline comparison; beam and resampling effects are not isolated.",
                               "Whole-file decoding; no live capture, bridge segmentation or end-to-end latency measurement.",
                               "Timing confidence is not validated against human word annotations."]}
    for case in json.loads(args.manifest.read_text(encoding="utf-8-sig"))["cases"]:
        for rate in (44100, 48000):
            audio = load_audio(case["audio"], rate)
            for name, cls, beam, words in (("before", baseline.Resampler, 3, False), ("after", Resampler, args.after_beam, True)):
                converter = cls(rate)
                chunks = [converter.process(audio[i:i+rate//10]) for i in range(0, len(audio), rate//10)]
                chunks.append(converter.flush())
                signal = np.concatenate(chunks)
                start = time.perf_counter()
                segments, info = model.transcribe(signal, language=case["language"], beam_size=beam,
                    condition_on_previous_text=False, word_timestamps=words, vad_filter=True,
                    hallucination_silence_threshold=1.0 if words else None)
                decoded = list(segments)
                text = " ".join(s.text.strip() for s in decoded)
                ref, hyp = units(case["reference"], case["language"]), units(text, case["language"])
                item = {"id": case["id"], "kind": case.get("kind"), "language": case["language"],
                        "input_rate": rate, "pipeline": name, "beam": beam,
                        "word_timestamps": words, "reference": case["reference"], "hypothesis": text,
                        "metric": "CER" if case["language"] == "zh" else "WER",
                        "edit_distance": distance(ref, hyp), "reference_units": len(ref),
                        "error_rate": distance(ref, hyp) / max(1, len(ref)),
                        "seconds": time.perf_counter()-start,
                        "duration": len(signal)/16000, "words": sum(len(s.words or []) for s in decoded),
                        "audio_sha256": hashlib.sha256(Path(case["audio"]).read_bytes()).hexdigest()}
                report["cases"].append(item)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(case["id"], rate, name, item["metric"], round(item["error_rate"], 4), flush=True)


if __name__ == "__main__":
    main()
