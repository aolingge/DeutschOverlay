"""Local model paths and explicit missing-model checks."""

from __future__ import annotations

import os
import sys
from pathlib import Path


MODEL_FILES = {
    "whisper-small": ("model.bin", "config.json", "tokenizer.json"),
    "opus-en-de": ("model.bin", "config.json", "source.spm", "target.spm", "vocab.json"),
    "opus-zh-de": ("model.bin", "config.json", "source.spm", "target.spm", "vocab.json"),
}


class MissingModelError(FileNotFoundError):
    """A local model must be prepared before offline inference."""


def default_model_root() -> Path:
    override = os.environ.get("DEUTSCH_OVERLAY_MODELS")
    if override:
        return Path(override).expanduser()
    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).resolve().parent / "models"
        if bundled.is_dir():
            return bundled
    source_checkout = Path(__file__).resolve().parents[2] / "models"
    if source_checkout.is_dir():
        return source_checkout
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DeutschOverlay" / "models"


class ModelStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or default_model_root()

    def require(self, name: str) -> Path:
        if name not in MODEL_FILES:
            raise ValueError(f"unsupported model: {name}")
        path = self.root / name
        missing = [filename for filename in MODEL_FILES[name] if not (path / filename).is_file()]
        if missing:
            recovery = "请重新完整解压发行包" if getattr(sys, "frozen", False) else "请运行 scripts/prepare-models.ps1"
            raise MissingModelError(f"{name} 模型缺失（{', '.join(missing)}）；{recovery}")
        return path
