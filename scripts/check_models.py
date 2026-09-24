"""Fail a release build if any required local model file is absent."""

import sys
from pathlib import Path

from deutsch_overlay.models import ModelStore


def main() -> None:
    store = ModelStore(Path(sys.argv[1]))
    for name in ("whisper-small", "opus-en-de", "opus-zh-de"):
        store.require(name)


if __name__ == "__main__":
    main()
