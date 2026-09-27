# 第三方组件与许可 / Third-party notices

本仓库**自有的源代码**采用 [MIT 许可](LICENSE)。下表列出的组件与模型**不是**本项目的作品，打包发行时各自遵循其上游许可。版本号取自本次构建实际冻结进 `dist/DeutschOverlay/` 的包。

The source code authored for this project is MIT licensed. The components and
models below are third-party works and remain under their own upstream
licenses. Versions are the ones actually frozen into the release package.

## 运行时组件

| 组件 | 版本 | 许可 | 上游 |
| --- | --- | --- | --- |
| PySide6 / PySide6-Essentials / PySide6-Addons / shiboken6 (Qt 6) | 6.11.2 | LGPL-3.0-only（另有商业许可） | [qt.io](https://www.qt.io/licensing/open-source-lgpl-obligations) |
| faster-whisper | 1.2.1 | MIT | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| CTranslate2 | 4.8.2 | MIT | [OpenNMT/CTranslate2](https://github.com/OpenNMT/CTranslate2) |
| Transformers | 5.17.0 | Apache-2.0 | [huggingface/transformers](https://github.com/huggingface/transformers) |
| tokenizers | 0.23.2 | Apache-2.0 | [huggingface/tokenizers](https://github.com/huggingface/tokenizers) |
| sentencepiece | 0.2.2 | Apache-2.0 | [google/sentencepiece](https://github.com/google/sentencepiece) |
| sacremoses | 0.2.0 | MIT | [hplt-project/sacremoses](https://github.com/hplt-project/sacremoses) |
| ONNX Runtime | 1.30.0 | MIT | [microsoft/onnxruntime](https://github.com/microsoft/onnxruntime) |
| PyAV (`av`) / FFmpeg | 18.1.0 | BSD-3-Clause（FFmpeg 为 LGPL/GPL，取决于构建） | [PyAV-Org/PyAV](https://github.com/PyAV-Org/PyAV) |
| NumPy | 2.5.3 | BSD-3-Clause | [numpy/numpy](https://github.com/numpy/numpy) |
| SoundCard | 0.4.6 | BSD-3-Clause | [bastibe/SoundCard](https://github.com/bastibe/SoundCard) |
| Silero VAD（`silero_vad_v6.onnx`，随 faster-whisper 分发） | v6 | MIT | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) |
| huggingface_hub / hf_xet | 1.33.0 / 1.6.0 | Apache-2.0 | [huggingface/huggingface_hub](https://github.com/huggingface/huggingface_hub) |
| protobuf | 7.36.2 | BSD-3-Clause | [protocolbuffers/protobuf](https://github.com/protocolbuffers/protobuf) |
| keyring | 25.7.0 | MIT | [jaraco/keyring](https://github.com/jaraco/keyring) |
| PyYAML / click / typer / rich / packaging / filelock / regex / safetensors / tqdm / tomli | 见 `dist/DeutschOverlay/_internal/*.dist-info` | MIT / BSD / Apache-2.0 各自适用 | PyPI |
| Azure Cognitive Services Speech SDK | 1.51.2 | Microsoft 软件许可条款（专有） | [Azure Speech SDK](https://learn.microsoft.com/azure/ai-services/speech-service/speech-sdk) |
| NVIDIA CUDA 运行库：cuDNN / cuBLAS / NVRTC | 9.26.0.51 / 12.9.2.10 / 12.9.86 | NVIDIA 软件许可协议（CUDA Toolkit EULA） | [NVIDIA CUDA Toolkit](https://docs.nvidia.com/cuda/eula/index.html) |
| CPython 运行时 | 3.12 | PSF-2.0 | [python.org](https://www.python.org/) |

## 模型

模型来源、上游仓库、转换时提交与许可见 [MODEL_SOURCES.md](MODEL_SOURCES.md)。摘要：Whisper small（MIT）、OPUS-MT en→de（CC BY 4.0，作者 Jörg Tiedemann、Santhosh Thotalingal）、OPUS-MT zh→de（Apache-2.0）。

## 关于 LGPL（Qt / PySide6）

发行包以**动态链接**方式使用 Qt：`PySide6`、`shiboken6` 与 Qt 运行库以独立的 DLL 形式放在 `_internal/` 目录中，未被静态链接进 `DeutschOverlay.exe`。任何人可以用自己构建的兼容版本替换这些 DLL，从而行使 LGPL-3.0 授予的重新链接权利。Qt 源码可从 [download.qt.io](https://download.qt.io/) 获取。

## 关于 CUDA 运行库

发行包内含 NVIDIA 的 cuDNN、cuBLAS 与 NVRTC 运行库，用于 GPU 加速。这些文件按 NVIDIA CUDA Toolkit EULA 的再分发条款随应用一起提供，仅作为本应用的一部分使用，不得单独提取再用。若你所在的环境不允许再分发这些库，请使用不含 `nvidia` 目录的构建（此时应用会回退到 CPU 识别）。

## 再分发者须知

若你要再次分发本应用或其修改版，请：

1. 保留本文件、`LICENSE` 与 `MODEL_SOURCES.md`；
2. 保留各模型随附的许可与署名资料；
3. 一并保留 `dist/DeutschOverlay/_internal/*.dist-info/licenses/` 下的上游许可文本；
4. 不要移除 Qt 的动态链接结构。
