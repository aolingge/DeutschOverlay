"""Resume official Hugging Face downloads and verify LFS SHA-256."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from urllib.parse import quote

import requests
from huggingface_hub import HfApi


CHUNK_BYTES = 8 * 1024 * 1024


def download_file(repo: str, revision: str, filename: str, target: Path, size: int, sha256: str | None) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size == size:
        if sha256 is None or file_sha256(target) == sha256:
            return target
    partial = target.with_name(target.name + ".part")
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{quote(filename)}"
    while (partial.stat().st_size if partial.exists() else 0) < size:
        offset = partial.stat().st_size if partial.exists() else 0
        end = min(offset + CHUNK_BYTES, size) - 1
        for attempt in range(6):
            try:
                response = requests.get(url, headers={"Range": f"bytes={offset}-{end}"}, timeout=(20, 90))
                response.raise_for_status()
                if response.status_code != 206 or response.headers.get("Content-Range") != f"bytes {offset}-{end}/{size}":
                    raise RuntimeError("server returned an unexpected byte range")
                block = response.content
                if len(block) != end - offset + 1:
                    raise RuntimeError("incomplete byte range")
                with partial.open("ab") as stream:
                    stream.write(block)
                print(f"{repo}/{filename}: {end + 1}/{size}", flush=True)
                break
            except (requests.RequestException, RuntimeError):
                if attempt == 5:
                    raise
                time.sleep(min(2 ** attempt, 15))
    if partial.stat().st_size != size:
        raise RuntimeError("download size mismatch")
    if sha256 is not None and file_sha256(partial) != sha256:
        partial.unlink()
        raise RuntimeError("model SHA-256 mismatch")
    partial.replace(target)
    return target


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def download_snapshot_files(repo: str, names: list[str], destination: Path) -> str:
    info = HfApi().model_info(repo, files_metadata=True)
    siblings = {s.rfilename: s for s in info.siblings}
    for name in names:
        sibling = siblings[name]
        sha = sibling.lfs.sha256 if sibling.lfs else None
        download_file(repo, info.sha, name, destination / name, sibling.size, sha)
    return info.sha
