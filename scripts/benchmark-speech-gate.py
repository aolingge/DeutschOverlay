"""Compare actual CLI speech segmentation without playback or capture.

Reports clip counts for reference recordings and controlled non-speech inputs.
Clip acceptance is not WER or proof of complete human speech boundaries.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from faster_whisper.audio import decode_audio

from deutsch_overlay.audio import PositionedSpeechSegmenter
from deutsch_overlay.browser_cli import build_speech_segmenter


def evaluate(audio, factory):
    segmenter = factory(sample_rate=16000, frame_samples=1600, silence_seconds=.5,
                        max_seconds=7, threshold=.004)
    spans = []
    start = time.perf_counter()
    for offset in range(0, len(audio), 1600):
        frame = audio[offset:offset+1600]
        spans.extend(segmenter.push_positioned(np.pad(frame, (0, 1600-len(frame)))))
    tail = segmenter.flush_positioned()
    if tail:
        spans.append(tail)
    return {"clips": len(spans), "processing_seconds": time.perf_counter()-start,
            "spans": [{"start": s.start_sample, "end": min(len(audio), s.end_sample),
                       "forced_cut": s.forced_cut, "overlap": s.overlap} for s in spans]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"cases": [], "limitations": ["No playback or live capture.",
              "Speech clip acceptance does not establish WER or correct word boundaries.",
              "Synthetic tone/noise is not representative of all music."]}
    fixtures = []
    for case in json.loads(args.manifest.read_text(encoding="utf-8-sig"))["cases"]:
        audio = decode_audio(case["audio"], sampling_rate=16000)
        fixtures.append((case["id"], audio, {"language": case["language"], "kind": "human",
                         "audio_sha256": hashlib.sha256(Path(case["audio"]).read_bytes()).hexdigest()}))
        fixtures.append((case["id"]+"-quiet", audio * .1, {"language": case["language"],
                         "kind": "human-quiet", "gain": .1}))
    position = np.arange(128000) / 16000
    for name, audio in (("silence", np.zeros(128000)),
                        ("tone-440", .1 * np.sin(2*np.pi*440*position)),
                        ("white-noise", np.random.default_rng(20261004).normal(0, .02, 128000))):
        fixtures.append((name, audio.astype(np.float32), {"kind": "synthetic-nonspeech"}))
    for name, audio, metadata in fixtures:
        item = {"id": name, **metadata, "duration": len(audio)/16000}
        for mode, factory in (("energy", PositionedSpeechSegmenter), ("silero", build_speech_segmenter)):
            item[mode] = evaluate(audio, factory)
        report["cases"].append(item)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(name, "clips", item["energy"]["clips"], "->", item["silero"]["clips"], flush=True)


if __name__ == "__main__":
    main()
