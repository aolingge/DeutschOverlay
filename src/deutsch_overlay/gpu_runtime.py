"""Make NVIDIA wheel DLLs discoverable before loading CTranslate2 on Windows."""

from __future__ import annotations

import importlib
import os
from pathlib import Path


_DLL_HANDLES: list[object] = []
_DIRECTORIES: list[Path] = []


def prepare_cuda_dlls() -> tuple[Path, ...]:
    if _DLL_HANDLES:
        return tuple(_DIRECTORIES)
    if not hasattr(os, "add_dll_directory"):
        return ()
    for package_name in ("nvidia.cudnn", "nvidia.cublas", "nvidia.cuda_nvrtc"):
        try:
            package = importlib.import_module(package_name)
        except ImportError:
            continue
        for package_path in package.__path__:
            binary_dir = Path(package_path) / "bin"
            if binary_dir.is_dir():
                _DLL_HANDLES.append(os.add_dll_directory(str(binary_dir)))
                _DIRECTORIES.append(binary_dir)
    return tuple(_DIRECTORIES)
