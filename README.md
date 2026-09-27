# Deutsch Overlay

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2011-0078D4)](#下载与安装)
[![Python](https://img.shields.io/badge/python-3.12-3776AB)](pyproject.toml)
[![CI](https://github.com/aolingge/DeutschOverlay/actions/workflows/ci.yml/badge.svg)](https://github.com/aolingge/DeutschOverlay/actions/workflows/ci.yml)

Windows 11 上的德语实时字幕条。它识别电脑正在播放的声音：德语直接显示德语；英语和中文翻译成德语。适合无边框全屏游戏和普通视频。

![德语单行与中德双语字幕条](docs/assets/windows-overlay-bilingual.png)

## 下载与安装

到 [Releases](https://github.com/aolingge/DeutschOverlay/releases/latest) 下载 `DeutschOverlay-0.1.0-win64.7z`（约 1.5 GB，解压后约 3 GB）：

1. 用 7-Zip、WinRAR、Bandizip 或 Windows 11 24H2 及以上的资源管理器把压缩包**完整解压**到一个目录；
2. 双击解压目录里的 `DeutschOverlay.exe`；
3. 首次运行会打开设置窗口，选好字幕内容和播放设备即可，应用会留在系统托盘。

- **不要只复制 EXE**：识别需要与它同目录的 `models` 文件夹。
- 系统要求：Windows 11 x64、约 6 GB 可用磁盘空间。没有 NVIDIA 显卡或显存不足时会自动回退到 CPU（更慢）。
- 校验下载：`Get-FileHash .\DeutschOverlay-0.1.0-win64.7z -Algorithm SHA256`，与 release 页面附带的 `SHA256SUMS.txt` 对照。

## 主要功能

- 德语识别，以及英语、中文翻译成德语；语言可自动判断，也可固定成当前视频的语言。
- **本地模式离线可用**，不录音、不上传；在线 Azure 模式按需开启，开启前不会连接。
- 字幕条九宫格定位、解锁拖动并记忆自定义位置；四种外观预设，背景/文字/原文/边框颜色、不透明度、边框、圆角、内边距、宽度、字号均可调。
- 自动跟随系统默认播放设备，也可手动指定；设备断开时提示“暂不可用”并等待重连，不会暗中切换。
- 全局快捷键、系统托盘、单实例、学习记录（最近 200 条，只存在于内存）。

## 使用

1. 通过桌面或开始菜单的“Deutsch Overlay”快捷方式打开；便携包用户可直接打开完整文件夹内的 `DeutschOverlay.exe`。程序会显示设置窗口，并留在系统托盘。
2. 默认打开“字幕”页，可直接选择“仅德语”或“德语 + 原文”、声音语言、播放设备和字幕位置。若不确定播放设备，保留“系统默认播放设备”；运行中切换系统默认扬声器或耳机时，应用会自动跟随。切换瞬间尚未说完的一句可能丢失，已显示的上一句仍按原时限淡出。手动指定设备则保持原选择；断开时会显示“暂不可用”并等待重连，不会暗中改回默认设备。设备列表有变化时可点“刷新设备”。
3. 启动游戏或视频。语音出现时，字幕条会在所选位置显示；安静一段时间后自动隐去。
4. 播放中文视频时，“德语 + 原文”会显示德语译文和中文原文；播放德语内容时只显示一遍德语。“隐藏字幕”和“暂停识别”可在字幕页直接切换。
5. 在“字幕位置”中选择屏幕九宫格位置。点“解锁并拖动”后，字幕条下方会出现“德语 + 原文”“设置”“完成”控制栏；拖动后点“完成”锁定。也可点“恢复下方位置”复位。锁定后控制栏消失，鼠标操作会穿过字幕条。显示器连接、主屏或可用区域变化时，字幕条会重新适配当前屏幕；原显示器恢复后，会尝试回到保存的自定义位置。
6. “外观”页提供深色、浅色、透明文字和描边四种预设。还可分别选择背景、德语文字、原文和边框颜色，以及不透明度、边框宽度、圆角、内边距、字幕宽度与字体大小。窗口底部的“应用并预览”始终可用；点击后保存设置并显示示例字幕。
7. 设置窗口的状态文字会显示当前正在监听的播放设备，“输入状态”会显示是否收到电脑播放声。若播放视频后仍显示“未检测到”，先核对播放设备和系统音量；若显示“正在接收”但没有字幕，则检查声音语言和语音内容。预览按钮可单独确认字幕条是否能显示。
8. 点击左侧“学习记录”可回看已完成的德语字幕和对应原文，并可复制全部或清空。记录最多保留最近 200 条，只在本次运行的内存中存在；关闭应用后自动消失。临时字幕不会写入记录。

快捷键：`Ctrl+Alt+1` 切换原文对照；`Ctrl+Alt+2` 显示或隐藏字幕；`Ctrl+Alt+3` 在自动、德语、英语、中文之间切换；`Ctrl+Alt+4` 暂停或继续识别。隐藏字幕后再次显示时，会立即恢复仍在显示时限内的字幕；时限已过则不会弹出旧句。调整字幕位置后重新锁定也会恢复有效字幕。在设置里手动切换播放设备、切换语言或暂停识别时会清掉旧字幕。若快捷键被游戏占用，可用托盘菜单操作。

托盘可用时，关闭设置窗口会把它收起；需要退出请用托盘菜单的“退出”。托盘不可用时，关闭设置窗口会退出应用。再次打开 EXE 会唤出已有的设置窗口，不会同时运行两个识别实例。

## 本地与在线模式

程序每次启动都进入本地模式。本地模型随完整发行文件夹放在 `models` 目录，不需要持续联网。若提示缺少模型，请重新取得并完整解压发行文件夹，确认 `DeutschOverlay.exe` 旁边有 `models` 文件夹；不要只复制 EXE。

完整文件夹约 3 GB，包含本地模型和 NVIDIA GPU 运行库。只复制 EXE 无法运行识别。在 PowerShell 中可运行 `Start-Process .\DeutschOverlay.exe -ArgumentList '--self-test' -Wait -PassThru -WindowStyle Hidden`；退出码为 0 表示本地模型和语音检测器可加载。

本机安装脚本 `scripts/install-local.ps1` 会把**已构建的完整文件夹**复制到用户目录 `%LOCALAPPDATA%\Programs\DeutschOverlay` 的独立版本目录，逐文件校验 SHA-256 并用安装目录中的模型自检，再更新桌面及开始菜单快捷方式。回采自检在没有可用播放设备时只给警告，不妨碍安装；打开应用后仍需选择并检查播放设备。此安装路径独立于源码仓库的 `dist`，之后重新构建仓库不会打断已安装版本。重复运行会校验并复用相同的完整包；中断的复制会继续完成并重验。更新时会保留旧版本，避免删除正在使用的文件；旧版本需要在确认不再使用后由用户自行清理。

若需要排查冻结版是否能打开播放设备，可设置环境变量 `DEUTSCH_OVERLAY_SELF_TEST_REPORT` 为一个本机文本文件路径，再用同样方式传入 `--audio-self-test`。退出码为 0 表示已取得环回音频帧；未播放声音时峰值为 0 是正常的。该检查不会识别、上传或保存语音。

在线模式是可选项，需要你自行在 Microsoft Azure 创建 Speech 资源，在“在线服务”页填入区域和密钥并点击“保存在线凭据”，然后手动选择“在线 · Azure”并应用。密钥存入 Windows 凭据存储，不写入设置文件。启用在线模式后，电脑播放声会发送给 Azure，可能产生费用。应用内的每日分钟上限只限制应用发送的音频量，不能代替 Azure 账单预算。建议同时在 Azure 设置预算提醒。

如果先选择了在线模式、随后才保存凭据，需再次点击“应用设置”才会重新开始识别；应用不会在保存凭据时自动开启可能收费的连接。

在线模式先检查本地凭据和每日额度，然后等待播放设备出现可检测的声音再建立云连接；持续安静约 5 秒会停流，下一段有声内容会重新连接。背景音乐和音效也算播放声，可能消耗在线额度；设备音量极低、初次连接较慢或网络异常时，仍可能漏掉句首或停止会话。用户可切回本地模式，或在 Azure 控制台查看服务侧实际用量。

旧版曾把 Azure 区域和密钥作为两项 Windows 凭据保存。新版会优先读取成组保存的凭据，但不会自动删除旧条目；若要移除旧密钥，请在 Windows“凭据管理器”中检查 `DeutschOverlay.AzureSpeech` 下的 `region` 和 `key` 项，确认不再需要后自行删除。应用不会在升级时读取、打印或上传旧密钥用于清理。

想尽快看到译文时，在线模式先把“声音语言”固定成当前视频或游戏的语言（例如中文）。程序会尝试显示识别中的临时德语译文，整句完成后再更新。若选择“自动识别”，Azure 的多语言翻译通常只给最终译文。本地模式会在开始监听前预热识别模型；连续语音约 2 秒后尝试显示颜色稍淡的临时字幕，句尾静音约 0.3 秒后用最终结果替换，最长约 7 秒切片，以减少单词在句中被强制截断。临时文字可能随上下文修正；首次启动的预热会增加等待时间。实际延迟还取决于设备、网络、声音质量与句长，不能保证固定毫秒数。

若 GPU 模型在预热或识别时发生运行错误，应用会尝试切换 CPU 并重试当前片段；CPU 速度可能较慢，状态栏会提示切换。在线模式安静约 5 秒后的自动停流会短暂等待最后一条识别结果，网络异常时仍可能漏句。

[Azure 官方价格页](https://azure.microsoft.com/en-us/pricing/details/speech/)目前列出 Speech Translation F0 每月 5 小时免费额度。付费价格会随区域、账户、币种和日期变化；创建资源前请以该页面和你的 Azure 账单设置为准。

## 限制

- 程序监听选定播放设备的混合声音，不区分游戏、视频和系统通知，也不监听麦克风。如果某个程序在 Windows 音量混合器中单独指定了另一台播放设备，请在本应用中手动选择那台设备；当前版本不会同时监听多个播放端点。
- 无边框全屏是目标游戏模式。WASAPI 回采依赖选定设备的共享模式播放；独占全屏、独占音频、部分反作弊游戏和受保护视频可能阻止覆盖显示或音频采集。
- 背景音乐、音效、多人同时说话会影响识别；约 2–3 秒是优化目标，具体延迟以实测为准。
- 字幕宽度会受当前屏幕宽度限制。极端大字号和很长的双语字幕会自动缩小显示字号；仍放不下时，屏幕上的文字会在末尾省略，下一句会按原设置重新排版。
- 不保存录音；学习记录默认不写入磁盘。需要长期保存时，可手动复制学习记录到自己的文档。

## 从源码运行与打包

需要 Python 3.12。运行环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[local,gpu,online,dev]'
$env:DEUTSCH_OVERLAY_MODELS = (Join-Path (Get-Location) 'models')
.\.venv\Scripts\python.exe -m deutsch_overlay.app
```

模型转换需要 PyTorch；开发者可创建带系统站点包的 `.model-venv` 并安装 `ctranslate2`、`transformers`、`sentencepiece`、`huggingface-hub`、`requests`，再运行 `scripts/prepare-models.ps1`。模型大文件从上游按区间续传，并与上游 SHA-256 对照。Windows EXE 的构建命令是 `scripts/build-exe.ps1`，输出在 `dist/DeutschOverlay/`；构建后可运行 `scripts/install-local.ps1` 更新本机安装。

## 验证

常规测试运行 `python -m pytest -q --cov=deutsch_overlay --cov-report=term`。本机有德、英、中 Windows SAPI 语音时，可设置环境变量 `DEUTSCH_TEST_REAL_MODELS=1` 再运行 `python -m pytest -q tests/test_real_models.py`，用真实本地模型识别三段合成语音。若还设置 `DEUTSCH_TEST_LIVE_LOOPBACK=1`，测试会在默认播放设备上**播放一小段声音**并验证 Windows 回采、语音分段和德语字幕。未设置时，这些较慢且依赖设备的测试会跳过。在线 Azure 仍需使用自己的资源与密钥，在实际网络下单独验证。

在真实 Windows 图形平台下运行 `$env:QT_QPA_PLATFORM='windows'; $env:DEUTSCH_TEST_REAL_MODELS='1'; $env:DEUTSCH_TEST_LIVE_LOOPBACK='1'; python -m pytest -q -s tests/test_live_overlay.py`，会播放德语及中文测试 MP4，并检查字幕实际到达覆盖层。`python scripts/verify-windows-overlay.py` 会生成不包含桌面内容的字幕示例截图。两项测试都需要可用的 Windows 桌面会话和播放设备。

对安装版做界面黑盒检查可运行 `python scripts/qa-installed-app.py <安装版 DeutschOverlay.exe 完整路径> --output docs/assets/qa-installed`。脚本用隔离设置启动安装版，通过 Windows UI Automation 点击预览并读取真实字幕窗口，再播放德语合成视频核对字幕；不会读取或更改平时使用的设置。

安装 FFmpeg 和 FFplay 后，运行 `python -m pytest -q -s tests/test_video_playback.py`（同样需设置上述两个环境变量），可自动制作德、英、中三段已知台词的 MP4，用 FFplay 从默认播放设备播放，检查真实系统回采、自动语言判断、原文识别和德语翻译。测试会打印音频峰值和**已截取语音的模型处理耗时**；该数字不包含视频播放、句尾静音等待和设备初始化，不能当作屏幕字幕的总延迟。此测试播放的是合成语音，真人口音、背景音乐和游戏音效仍需用对应样本验证。

模型来源与许可：[faster-whisper small](https://huggingface.co/Systran/faster-whisper-small)、[OPUS-MT 英语到德语](https://huggingface.co/Helsinki-NLP/opus-mt-en-de)、[OPUS-MT 中文到德语](https://huggingface.co/Helsinki-NLP/opus-mt-zh-de)。分发前应保留各模型随附的许可和署名资料。

## 贡献

- 提 Issue 时请写明：Windows 版本、显卡与显存、复现步骤、设置窗口状态栏显示的文字。**不要**附带真实 Azure 密钥、录音文件或含用户名的完整路径。
- 提 PR 前请运行 `python -m pytest -q --cov=deutsch_overlay --cov-report=term`，并在 [CHANGELOG.md](CHANGELOG.md) 里记录面向用户的改动。
- 安全与隐私问题请按 [SECURITY.md](SECURITY.md) 私下报告，不要开公开 Issue。

## 许可

本仓库自有的源代码采用 [MIT 许可](LICENSE)。

发行包内打包的第三方组件（Qt/PySide6、ONNX Runtime、CTranslate2、Transformers、NVIDIA CUDA 运行库等）与三套模型**不属于本项目的作品**，各自遵循其上游许可，完整清单见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) 与 [MODEL_SOURCES.md](MODEL_SOURCES.md)。再次分发时请一并保留这些许可文件。

## English

**Deutsch Overlay** is a Windows 11 overlay that turns whatever your PC is playing into a live German caption bar: German speech is shown as-is, while English and Chinese speech is translated into German. It targets borderless-fullscreen games and ordinary video.

- **Download**: grab `DeutschOverlay-0.1.0-win64.7z` from [Releases](https://github.com/aolingge/DeutschOverlay/releases/latest), extract the archive completely (the `models` folder next to the executable is required), then run `DeutschOverlay.exe`.
- **Privacy**: local mode is fully offline (faster-whisper + CTranslate2) and never records or uploads audio. Online mode uses Azure Speech and is opt-in, with credentials kept in Windows Credential Manager. See [SECURITY.md](SECURITY.md).
- **Source layout**: `src/deutsch_overlay/` (app, audio capture, engines, overlay, settings UI), `tests/` (pytest, ~200 tests, 90% coverage), `scripts/` (model preparation, EXE build, local install, QA harness). The UI is in Chinese; the setup and packaging commands are under [从源码运行与打包](#从源码运行与打包) above.
- **Verification**: [docs/verification.md](docs/verification.md) records exactly what was tested on real hardware and what was not. It is written in Chinese and is deliberately honest about the gaps (real games, human voices, monitor hot-plug, live Azure accounts).
- **License**: MIT for this project's source; bundled third-party components and models keep their own upstream licenses — see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

Issues and pull requests are welcome. Please keep credentials and recorded audio out of reports.
