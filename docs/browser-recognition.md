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
| `--turbo-model-path` | 注册可在扩展中选择的本地 Turbo 目录；不会下载。与 `--asr-model-path` 指向同一路径时默认选中 Turbo |
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

词表默认留空。只对明确或已确认的识别语言生效，自动初探不会用混合词表。词表作用于当前桥运行周期；切换学习主题后可先停止识别，再在扩展中修改，或更新启动词表并重启桥。提示可能使模型误插术语，不保证降低错误率。不使用自动生成的视频标题提示，避免把标题内容错当成实际语音。

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

## 扩展中的识别设置与重试

扩展通过带有本机令牌的 `GET/POST /v1/settings` 读取及修改设置。正文直接包含要修改的字段：`modelProfile`、`chineseScript`、`hotwords`、`translationContext`。模型选项仅来自服务端注册的 `profiles`；网页不能提交路径。设置只存于当前桥进程内，重启后重新使用 CLI 配置。公共健康检查不返回词表或模型路径。

先停止所有识别会话再修改。已停止会话仍有推理正在结束时会暂时返回冲突，等它结束后再试。切换模型不会自动下载或立即加载，新会话创建时加载并预热；因此设置中的 `modelReady=false` 和 `device=unloaded` 不代表配置失败。推理实际启用 CUDA 后才显示 `cuda`，GPU 回退会反映为 `cpu` 和相应提示。

最近识别片段支持 `POST /v1/session/{id}/retry`，正文为 `{ "segmentId": "...", "timelineEpoch": 0 }`。服务端仅在内存保留最多 8 个最终音频片段、总计最多 60 秒，重识别使用 beam size 5，沿用原片段 ID 和模型时间坐标。拖动进度、更换会话或音频过期后返回明确错误；不会为重试绕过原生字幕检查，也不录音到磁盘。更宽的 beam 不是准确率保证。

重试改变分句数量时，已不存在的旧片段会发布同 ID 的 `removed:true` 修订；客户端必须删除该字幕，避免把旧句子留下。正常字幕的 `removed` 为 `false`。

## 不确定性、译文上下文与延迟

字幕保留 `avgLogprob` 和 `noSpeechProbability`，以 `uncertain` 和 `uncertaintyReasons` 提示可能有误：平均对数概率小于 -1.0，或无语音概率大于 0.6。缺少数值时标记 `scores_unavailable`，表示无法评估。这些是排查用启发式阈值，不是“正确率百分比”；无警告也不代表文字一定正确。翻译修订保留这些源语音信息。

识别和翻译使用独立工作线程，先显示原文。每个会话最多积压 8 个音频任务和 32 个翻译任务；超限跳过最旧的待处理任务并显示警告与丢弃计数。被跳过的翻译保留原文。拖动进度或关闭会话后，旧推理即使完成也不会发布修订。独立内部代次也覆盖网页没有提供新 epoch 的跳转。

可选 `translationContext=true` 将相邻的未结束语句组合后整体翻译：最多 3 段、240 字符、间隔不超过 800 毫秒。仅对英语和中文启用；完整句子直接翻译，等待下一片段最多 250 毫秒。组合译文共享给这些片段，并附 `translationGroupIds`；原文和时间不改写。它不从译文中猜测或截去所谓“前文前缀”。该选项默认关闭，效果仍应按具体内容评估。

每个会话最多缓存 128 条成功译文；跳转和会话结束会清理。已认证的会话及字幕响应包含队列长度、跳过计数、缓存命中计数、最近识别/翻译耗时与排队耗时。各项 P95 使用最近最多 128 次测量；它们是当前运行观测，不是固定性能承诺。识别空闲状态需要两个工作队列都处理完毕。

## 静音测量

`scripts/benchmark-recognition.py` 可比较本地模型，默认 CPU/int8；`--device cuda` 使用 int8_float16。可选 `--baseline-pcm` 保留旧重采样比较模式，省略后只评估当前实现。`--chinese-script simplified` 同时输出原始错误数、显示错误数以及参考和结果采用相同简繁转换后的错误数。重复的输入采样率不是独立录音，不能据此夸大样本量。

`scripts/benchmark-speech-gate.py` 比较当前真实 CLI 使用的分句检测器，在文件中处理参考音频、降音量副本、纯音、白噪声和静音。它不播放、不监听设备、不启动桥服务。产生语音片段不等于完整保留每个词，也不等于 WER 更低。

实时浏览器 ASR 仍要求当前视频或分 P 已可靠确认 `absent`。普通源码启动会读取或创建用户自己的本机握手，仅应由用户实际启用时运行；基准脚本不会读取握手或令牌。

## 官方资料

- [OpenCC](https://github.com/BYVoid/OpenCC)：Apache-2.0，本地字典转换，不修复听辨错误。
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)：MIT，模型加载、`hotwords` 和 VAD。
- [Silero VAD](https://github.com/snakers4/silero-vad)：MIT，语音活动检测。
- [Whisper Turbo](https://huggingface.co/openai/whisper-large-v3-turbo)：需要与实际用途相符的独立录音比较。
