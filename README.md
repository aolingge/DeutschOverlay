# Deutsch Overlay

Windows 11 上的德语实时字幕条。它识别电脑正在播放的声音：德语直接显示德语；英语和中文翻译成德语。适合无边框全屏游戏和普通视频。

## 使用

1. 打开 `DeutschOverlay.exe`。程序会显示设置窗口，并留在系统托盘。
2. 选择播放设备。若不确定，保留“系统默认播放设备”；耳机插拔后可点“刷新”。
3. 启动游戏或视频。语音出现时，字幕条会在屏幕下方显示；安静一段时间后自动隐去。
4. 在设置窗口可调整位置、宽度、字体、背景透明度和隐藏延迟。点“解锁/锁定字幕位置”后拖动字幕条，完成后再次锁定；位置跑出屏幕时点“字幕位置复位”。锁定状态下，鼠标操作会穿过字幕条。

快捷键：`Ctrl+Alt+1` 切换原文对照；`Ctrl+Alt+2` 显示或隐藏字幕；`Ctrl+Alt+3` 在自动、德语、英语、中文之间切换；`Ctrl+Alt+4` 暂停或继续识别。若快捷键被游戏占用，可用托盘菜单操作。

**退出时请用托盘菜单的“退出”**。关闭设置窗口只会把它收起。

## 本地与在线模式

程序每次启动都进入本地模式。本地模型随完整发行文件夹放在 `models` 目录，不需要持续联网。若提示缺少模型，请重新取得并完整解压发行文件夹，确认 `DeutschOverlay.exe` 旁边有 `models` 文件夹；不要只复制 EXE。

完整文件夹约 3 GB，包含本地模型和 NVIDIA GPU 运行库。只复制 EXE 无法运行识别。在 PowerShell 中可运行 `Start-Process .\DeutschOverlay.exe -ArgumentList '--self-test' -Wait -PassThru -WindowStyle Hidden`；退出码为 0 表示本地模型和语音检测器可加载。

在线模式是可选项，需要你自行在 Microsoft Azure 创建 Speech 资源，在设置窗口填入区域和密钥并点击“保存在线凭据”，然后手动切换到“在线”。密钥存入 Windows 凭据存储，不写入设置文件。启用在线模式后，电脑播放声会发送给 Azure，可能产生费用。应用内的每日分钟上限只限制应用发送的音频量，不能代替 Azure 账单预算。建议同时在 Azure 设置预算提醒。

## 限制

- 首版监听选定播放设备的混合声音，不区分游戏、视频和系统通知，也不监听麦克风。
- 无边框全屏是目标游戏模式。独占全屏、部分反作弊游戏和受保护视频可能阻止覆盖显示或音频采集。
- 背景音乐、音效、多人同时说话会影响识别；约 2–3 秒是优化目标，具体延迟以实测为准。
- 首版不保存录音和字幕历史。

## 从源码运行与打包

需要 Python 3.12。运行环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[local,gpu,online,dev]'
$env:DEUTSCH_OVERLAY_MODELS = (Join-Path (Get-Location) 'models')
.\.venv\Scripts\python.exe -m deutsch_overlay.app
```

模型转换需要 PyTorch；开发者可创建带系统站点包的 `.model-venv` 并安装 `ctranslate2`、`transformers`、`sentencepiece`、`huggingface-hub`、`requests`，再运行 `scripts/prepare-models.ps1`。模型大文件从上游按区间续传，并与上游 SHA-256 对照。Windows EXE 的构建命令是 `scripts/build-exe.ps1`，输出在 `dist/DeutschOverlay/`。

模型来源与许可：[faster-whisper small](https://huggingface.co/Systran/faster-whisper-small)、[OPUS-MT 英语到德语](https://huggingface.co/Helsinki-NLP/opus-mt-en-de)、[OPUS-MT 中文到德语](https://huggingface.co/Helsinki-NLP/opus-mt-zh-de)。分发前应保留各模型随附的许可和署名资料。
