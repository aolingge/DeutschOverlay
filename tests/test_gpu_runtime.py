from pathlib import Path

import pytest

from deutsch_overlay.gpu_runtime import prepare_cuda_dlls


def test_cuda_runtime_adds_packaged_nvidia_dll_directories():
    pytest.importorskip("nvidia.cudnn")
    pytest.importorskip("nvidia.cublas")
    directories = prepare_cuda_dlls()
    assert any("cudnn" in str(path).lower() for path in directories)
    assert any("cublas" in str(path).lower() for path in directories)
    assert all(Path(path).is_dir() for path in directories)
