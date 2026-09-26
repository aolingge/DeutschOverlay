# Deutsch Overlay

Windows 11 上的德语实时字幕条。它识别电脑正在播放的声音：德语直接显示德语；英语和中文翻译成德语。适合无边框全屏游戏和普通视频。

## 使用

1. 通过桌面或开始菜单的“Deutsch Overlay”快捷方式打开；便携包用户可直接打开完整文件夹内的 `DeutschOverlay.exe`。程序会显示设置窗口，并留在系统托盘。
2. 选择播放设备。若不确定，保留“系统默认播放设备”；运行中切换系统默认扬声器或耳机时，应用会自动跟随。切换瞬间尚未说完的一句可能丢失，已显示的上一句仍按原时限淡出。手动指定设备则保持原选择；断开时会显示“暂不可用”并等待重连，不会暗中改回默认设备。设备列表有变化时可点“刷新”。
3. 启动游戏或视频。语音出现时，字幕条会在所选位置显示；安静一段时间后自动隐去。
4. 在“字幕内容”中选择“仅德语”或“德语 + 原文”。播放中文视频时，后者显示德语译文和中文原文；播放德语内容时只显示一遍德语。
5. 在“字幕位置”中选择屏幕九宫格位置，也可选“自定义拖动”。点“解锁/锁定字幕位置”后拖动字幕条，完成后再次锁定；位置跑出屏幕时点“字幕位置复位”。锁定状态下，鼠标操作会穿过字幕条。
6. “背景样式”提供深色、浅色、透明文字和描边四种预设。还可分别选择背景、德语文字、原文和边框颜色，以及不透明度、边框宽度、圆角、内边距、字幕宽度与字体大小。点击“应用并预览”即可看到示例字幕；设定会保存到本机。
7. 设置窗口的状态文字会显示当前正在监听的播放设备，“输入状态”会显示是否收到电脑播放声。若播放视频后仍显示“未检测到”，先核对播放设备和系统音量；若显示“正在接收”但没有字幕，则检查声音语言和语音内容。预览按钮可单独确认字幕条是否能显示。

快捷键：`Ctrl+Alt+1` 切换原文对照；`Ctrl+Alt+2` 显示或隐藏字幕；`Ctrl+Alt+3` 在自动、德语、英语、中文之间切换；`Ctrl+Alt+4` 暂停或继续识别。隐藏字幕后再次显示时，会立即恢复仍在显示时限内的字幕；时限已过则不会弹出旧句。调整字幕位置后重新锁定也会恢复有效字幕。在设置里手动切换播放设备、切换语言或暂停识别时会清掉旧字幕。若快捷键被游戏占用，可用托盘菜单操作。

托盘可用时，关闭设置窗口会把它收起；需要退出请用托盘菜单的“退出”。托盘不可用时，关闭设置窗口会退出应用。再次打开 EXE 会唤出已有的设置窗口，不会同时运行两个识别实例。

## 本地与在线模式

程序每次启动都进入本地模式。本地模型随完整发行文件夹放在 `models` 目录，不需要持续联网。若提示缺少模型，请重新取得并完整解压发行文件夹，确认 `DeutschOverlay.exe` 旁边有 `models` 文件夹；不要只复制 EXE。

完整文件夹约 3 GB，包含本地模型和 NVIDIA GPU 运行库。只复制 EXE 无法运行识别。在 PowerShell 中可运行 `Start-Process .\DeutschOverlay.exe -ArgumentList '--self-test' -Wait -PassThru -WindowStyle Hidden`；退出码为 0 表示本地模型和语音检测器可加载。

本机安装脚本 `scripts/install-local.ps1` 会把**已构建的完整文件夹**复制到用户目录 `%LOCALAPPDATA%\Programs\DeutschOverlay` 的独立版本目录，逐文件校验 SHA-256 并用安装目录中的模型自检，再更新桌面及开始菜单快捷方式。回采自检在没有可用播放设备时只给警告，不妨碍安装；打开应用后仍需选择并检查播放设备。此安装路径独立于源码仓库的 `dist`，之后重新构建仓库不会打断已安装版本。重复运行会校验并复用相同的完整包；中断的复制会继续完成并重验。更新时会保留旧版本，避免删除正在使用的文件；旧版本需要在确认不再使用后由用户自行清理。

若需要排查冻结版是否能打开播放设备，可设置环境变量 `DEUTSCH_OVERLAY_SELF_TEST_REPORT` 为一个本机文本文件路径，再用同样方式传入 `--audio-self-test`。退出码为 0 表示已取得环回音频帧；未播放声音时峰值为 0 是正常的。该检查不会识别、上传或保存语音。

在线模式是可选项，需要你自行在 Microsoft Azure 创建 Speech 资源，在设置窗口填入区域和密钥并点击“保存在线凭据”，然后手动切换到“在线”。密钥存入 Windows 凭据存储，不写入设置文件。启用在线模式后，电脑播放声会发送给 Azure，可能产生费用。应用内的每日分钟上限只限制应用发送的音频量，不能代替 Azure 账单预算。建议同时在 Azure 设置预算提醒。

在线模式先检查本地凭据和每日额度，然后等待播放设备出现可检测的声音再建立云连接；持续安静约 5 秒会停流，下一段有声内容会重新连接。背景音乐和音效也算播放声，可能消耗在线额度；设备音量极低、初次连接较慢或网络异常时，仍可能漏掉句首或停止会话。用户可切回本地模式，或在 Azure 控制台查看服务侧实际用量。

旧版曾把 Azure 区域和密钥作为两项 Windows 凭据保存。新版会优先读取成组保存的凭据，但不会自动删除旧条目；若要移除旧密钥，请在 Windows“凭据管理器”中检查 `DeutschOverlay.AzureSpeech` 下的 `region` 和 `key` 项，确认不再需要后自行删除。应用不会在升级时读取、打印或上传旧密钥用于清理。

想尽快看到译文时，在线模式先把“声音语言”固定成当前视频或游戏的语言（例如中文）。程序会尝试显示识别中的临时德语译文，整句完成后再更新。若选择“自动识别”，Azure 的多语言翻译通常只给最终译文。本地模式会在开始监听前预热识别模型；连续语音约 2 秒后尝试显示颜色稍淡的临时字幕，句尾静音约 0.3 秒后用最终结果替换，最长约 7 秒切片，以减少单词在句中被强制截断。临时文字可能随上下文修正；首次启动的预热会增加等待时间。实际延迟还取决于设备、网络、声音质量与句长，不能保证固定毫秒数。

Azure 官方目前列有 Speech Translation F0 每月 5 小时免费额度；[官方说明](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-translation)给出的标价参考为每音频小时 2.50 美元。创建资源前请以[区域和账户对应的实时价格页](https://azure.microsoft.com/en-us/pricing/details/speech/)为准。

## 限制

- 程序监听选定播放设备的混合声音，不区分游戏、视频和系统通知，也不监听麦克风。如果某个程序在 Windows 音量混合器中单独指定了另一台播放设备，请在本应用中手动选择那台设备；当前版本不会同时监听多个播放端点。
- 无边框全屏是目标游戏模式。WASAPI 回采依赖选定设备的共享模式播放；独占全屏、独占音频、部分反作弊游戏和受保护视频可能阻止覆盖显示或音频采集。
- 背景音乐、音效、多人同时说话会影响识别；约 2–3 秒是优化目标，具体延迟以实测为准。
- 字幕宽度会受当前屏幕宽度限制。极端大字号和很长的双语字幕会自动缩小显示字号；仍放不下时，屏幕上的文字会在末尾省略，下一句会按原设置重新排版。
- 首版不保存录音和字幕历史。

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

安装 FFmpeg 和 FFplay 后，运行 `python -m pytest -q -s tests/test_video_playback.py`（同样需设置上述两个环境变量），可自动制作德、英、中三段已知台词的 MP4，用 FFplay 从默认播放设备播放，检查真实系统回采、自动语言判断、原文识别和德语翻译。测试会打印音频峰值和**已截取语音的模型处理耗时**；该数字不包含视频播放、句尾静音等待和设备初始化，不能当作屏幕字幕的总延迟。此测试播放的是合成语音，真人口音、背景音乐和游戏音效仍需用对应样本验证。

模型来源与许可：[faster-whisper small](https://huggingface.co/Systran/faster-whisper-small)、[OPUS-MT 英语到德语](https://huggingface.co/Helsinki-NLP/opus-mt-en-de)、[OPUS-MT 中文到德语](https://huggingface.co/Helsinki-NLP/opus-mt-zh-de)。分发前应保留各模型随附的许可和署名资料。
