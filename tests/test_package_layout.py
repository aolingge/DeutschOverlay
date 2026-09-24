import sys
import subprocess
from pathlib import Path

import pytest

from deutsch_overlay.models import MODEL_FILES, MissingModelError, ModelStore, default_model_root


def test_frozen_app_uses_model_folder_beside_exe(monkeypatch, tmp_path):
    exe = tmp_path / "DeutschOverlay.exe"
    exe.write_bytes(b"test")
    (tmp_path / "models").mkdir()
    monkeypatch.delenv("DEUTSCH_OVERLAY_MODELS", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert default_model_root() == tmp_path / "models"


def test_missing_bundled_model_names_exact_recovery_target(tmp_path):
    model_dir = tmp_path / "models" / "whisper-small"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(MissingModelError, match="model.bin"):
        ModelStore(tmp_path / "models").require("whisper-small")


def test_release_model_check_rejects_incomplete_and_accepts_complete_set(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_models.py"
    command = [sys.executable, str(script), str(tmp_path)]
    assert subprocess.run(command, capture_output=True).returncode != 0
    for name, filenames in MODEL_FILES.items():
        folder = tmp_path / name
        folder.mkdir()
        for filename in filenames:
            (folder / filename).write_bytes(b"fixture")
    assert subprocess.run(command, capture_output=True).returncode == 0
