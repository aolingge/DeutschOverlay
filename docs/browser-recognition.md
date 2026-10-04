# 浏览器桥：识别配置

更新日期：2026-10-04。以下参数用于 `python -m deutsch_overlay.browser_cli`，需要从更新后的源码运行。旧安装版不会自动更新。本次不更换桌面程序的默认识别选项。

默认使用已有 Whisper Small、中文简体显示，以及已有 faster-whisper 自带的 Silero 模型作为实时分句语音检测器。所有模型推理均本地执行，不会自动下载模型。

## 可用选项

| 参数 | 行为 |
| --- | --- |
| `--chinese-script simplified` | 默认繁转简，只影响中文识别输出；不改 native 字幕 |
| `--chinese-script traditional` | 转为繁体 |
| `--chinese-script raw` | 保留模型原始字形 |
| `--asr-model-path` | 选择已准备好的本地 CTranslate2 Whisper 模型目录；检查三个必要文件；GPU 回退继续使用同一模型 |
| `--hotwords-file` | UTF-8 JSON，`de/en/zh` 对应术语字符串，各不超过 1000 字符且不含控制字符 |
| `--speech-gate silero` | 默认，在实时分句前区分语音与非语音 |
| `--speech-gate energy` | 回到原音量阈值，便于兼容性比较 |
| `--no-gpu` | CPU 模式；大模型需要实测耗时 |

术语表示例，只放普通术语，不放密码或密钥：

```json
{
  "de": "CTranslate2, PyAV, Anki",
  "en": "CTranslate2, PyAV, Anki",
  "zh": "重采样, 语音识别, 德语"
}
```

词表默认留空。只对明确或已确认的识别语言生效，自动初探不会用混合词表。词表作用于当前桥运行周期；切换学习主题后应更新词表并重启桥。提示可能使模型误插术语，不保证降低错误率。不使用自动生成的视频标题提示，避免把标题内容错当成实际语音。

简繁转换保留可审计的原始文本：文件 JSON 中的 `rawText`，实时协议中的 `rawOriginal`。显示文字与识别词时间必须完整对应，跨词边界的字典转换破坏对应时清除词时间。使用的是 OpenCC 的 `t2s/s2t`，不做地区词汇替换或语义润色。声学置信度仍来自原模型。

## 配置示例

```powershell
.\.venv\Scripts\python.exe -m deutsch_overlay.browser_cli
# 可选本地模型；必须事先准备，下面是示例路径。
.\.venv\Scripts\python.exe -m deutsch_overlay.browser_cli --asr-model-path 'D:\Models\WhisperTurbo'
# 可选术语表。
.\.venv\Scripts\python.exe -m deutsch_overlay.browser_cli --hotwords-file '.\terms.json'
```

不要把模型目录误填成 Hugging Face 名称；本接口仅接受本地目录。缺文件会拒绝启动，不自动下载或偷偷切换其他模型。CTranslate2 不加载仓库 Python 代码，仍应使用可信来源及校验和。

## 静音测量

`scripts/benchmark-recognition.py` 可比较本地模型，默认 CPU/int8；`--device cuda` 使用 int8_float16。可选 `--baseline-pcm` 保留旧重采样比较模式，省略后只评估当前实现。`--chinese-script simplified` 同时输出原始错误数、显示错误数以及参考和结果采用相同简繁转换后的错误数。重复的输入采样率不是独立录音，不能据此夸大样本量。

`scripts/benchmark-speech-gate.py` 比较当前真实 CLI 使用的分句检测器，在文件中处理参考音频、降音量副本、纯音、白噪声和静音。它不播放、不监听设备、不启动桥服务。产生语音片段不等于完整保留每个词，也不等于 WER 更低。

实时浏览器 ASR 仍要求当前视频或分 P 已可靠确认 `absent`。普通源码启动会读取或创建用户自己的本机握手，仅应由用户实际启用时运行；基准脚本不会读取握手或令牌。

## 官方资料

- [OpenCC](https://github.com/BYVoid/OpenCC)：Apache-2.0，本地字典转换，不修复听辨错误。
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)：MIT，模型加载、`hotwords` 和 VAD。
- [Silero VAD](https://github.com/snakers4/silero-vad)：MIT，语音活动检测。
- [Whisper Turbo](https://huggingface.co/openai/whisper-large-v3-turbo)：需要与实际用途相符的独立录音比较。
