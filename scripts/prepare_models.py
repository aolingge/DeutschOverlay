"""Download pinned model snapshots and convert translation models once."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from download_verified import download_snapshot_files


MODEL_REPOS = {
    "whisper-small": "Systran/faster-whisper-small",
    "opus-en-de": "Helsinki-NLP/opus-mt-en-de",
    "opus-zh-de": "Helsinki-NLP/opus-mt-zh-de",
}
TOKENIZER_FILES = ["source.spm", "target.spm", "vocab.json", "tokenizer_config.json"]


def prepare(name: str, root: Path) -> Path:
    repo = MODEL_REPOS[name]
    destination = root / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if name == "whisper-small":
        revision = download_snapshot_files(
            repo, ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin", "README.md"], destination
        )
    else:
        from ctranslate2.converters import TransformersConverter

        source = root / "_sources" / name
        revision = download_snapshot_files(
            repo,
            ["config.json", "generation_config.json", "tokenizer_config.json", "source.spm", "target.spm", "vocab.json", "pytorch_model.bin", "README.md"],
            source,
        )
        converter = TransformersConverter(
            str(source),
            copy_files=TOKENIZER_FILES,
            trust_remote_code=False,
        )
        converter.convert(str(destination), quantization="int8", force=True)
        (destination / "MODEL_CARD.md").write_bytes((source / "README.md").read_bytes())
    (destination / "source.json").write_text(
        json.dumps({"repository": repo, "revision": revision}, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("models"))
    parser.add_argument("--model", choices=[*MODEL_REPOS, "all"], default="all")
    args = parser.parse_args()
    names = MODEL_REPOS if args.model == "all" else [args.model]
    for name in names:
        print(f"Preparing {name} from {MODEL_REPOS[name]}...")
        print(prepare(name, args.root))


if __name__ == "__main__":
    main()
