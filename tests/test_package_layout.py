import hashlib
import shutil
import sys
import subprocess
from pathlib import Path

import pytest

from deutsch_overlay.models import MODEL_FILES, MissingModelError, ModelStore, default_model_root

RELEASE_DOCUMENTS = (
    "README.md",
    "MODEL_SOURCES.md",
    "LICENSE",
    "THIRD-PARTY-NOTICES.md",
    "SECURITY.md",
    "CHANGELOG.md",
)


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


def test_release_bundle_ships_licence_and_notices():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "build-exe.ps1").read_text(encoding="utf-8")
    for document in RELEASE_DOCUMENTS:
        assert (root / document).is_file(), f"{document} is missing from the project root"
        assert document in script, f"build-exe.ps1 no longer copies {document} into the release bundle"


def run_powershell(arguments):
    return subprocess.run(
        ["pwsh", "-NoProfile", *arguments],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


needs_powershell = pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not available")


@needs_powershell
@pytest.mark.parametrize("script", ["install-local.ps1", "install-release.ps1", "build-exe.ps1"])
def test_packaging_scripts_parse(script):
    path = Path(__file__).resolve().parents[1] / "scripts" / script
    literal = str(path).replace("'", "''")
    result = run_powershell(["-Command", f"[void][scriptblock]::Create((Get-Content -LiteralPath '{literal}' -Raw))"])
    assert result.returncode == 0, result.stderr


@needs_powershell
def test_release_installer_refuses_an_archive_that_fails_its_checksum(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "install-release.ps1"
    archive = tmp_path / "DeutschOverlay-0.1.0-win64.7z"
    archive.write_bytes(b"fixture archive")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    accepted = run_powershell(["-File", str(script), "-Archive", str(archive), "-VerifyOnly"])
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert digest in accepted.stdout
    sums.write_text("0" * 64 + f"  {archive.name}\n", encoding="utf-8")
    rejected = run_powershell(["-File", str(script), "-Archive", str(archive), "-VerifyOnly"])
    assert rejected.returncode != 0
    assert "failed its checksum" in rejected.stdout + rejected.stderr

