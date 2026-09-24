import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from download_verified import download_file


def test_resume_download_checks_ranges_and_sha(tmp_path, monkeypatch):
    content = b"abcdefghijk"
    expected = hashlib.sha256(content).hexdigest()
    target = tmp_path / "model.bin"
    target.with_name("model.bin.part").write_bytes(content[:4])
    calls = []

    def fake_get(_url, headers, timeout):
        calls.append((headers["Range"], timeout))
        return SimpleNamespace(
            status_code=206,
            headers={"Content-Range": "bytes 4-10/11"},
            content=content[4:],
            raise_for_status=lambda: None,
        )

    monkeypatch.setattr("download_verified.requests.get", fake_get)
    assert download_file("repo/name", "rev", "model.bin", target, 11, expected) == target
    assert target.read_bytes() == content
    assert calls == [("bytes=4-10", (20, 90))]


def test_bad_hash_discards_partial(tmp_path, monkeypatch):
    target = tmp_path / "model.bin"
    target.with_name("model.bin.part").write_bytes(b"wrong")
    with pytest.raises(RuntimeError, match="SHA-256"):
        download_file("repo/name", "rev", "model.bin", target, 5, "0" * 64)
    assert not target.exists()
    assert not target.with_name("model.bin.part").exists()
