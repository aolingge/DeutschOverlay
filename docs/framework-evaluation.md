# 现成方案核对与复用决定

日期：2026-09-25。依据各项目的原始仓库和许可证文件；下面是集成判断，不是同机性能排名。

| 方案 | 已有能力 | 与本项目的关系 |
| --- | --- | --- |
| [LiveTranslate](https://github.com/TheDeathDragon/LiveTranslate)（[MIT](https://github.com/TheDeathDragon/LiveTranslate/blob/main/LICENSE)） | Windows WASAPI、Silero VAD、faster-whisper、可配置翻译 API、流式译文、覆盖层 | 场景最接近。可借鉴其翻译提供者和流式结果的边界；直接替换整套程序会重做现有设置、模型打包和已验证链路。 |
| [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit)（[Apache-2.0](https://github.com/QuentinFuxa/WhisperLiveKit/blob/main/LICENSE)） | 增量 ASR、翻译、WebSocket | 适合作为隔离的延迟对照。需增加服务进程和模型；其常用 [NLLB 权重](https://huggingface.co/facebook/nllb-200-distilled-600M)标为 CC-BY-NC-4.0，框架许可不能替代模型许可。 |
| [WhisperLive](https://github.com/collabora/WhisperLive)（[MIT](https://github.com/collabora/WhisperLive/blob/main/LICENSE)） | 实时识别服务器、临时和最终回调 | 可做后续可选识别后端，但当前单机 EXE 无需额外服务进程。 |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)（[Apache-2.0](https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE)） | Windows 流式识别与 VAD | 需先用同一批德英中游戏/视频语音和相同硬件比较准确率、字幕总延迟、模型体积；翻译与覆盖层仍需本项目承担。 |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp)（[MIT](https://github.com/ggml-org/whisper.cpp/blob/master/LICENSE)） | Windows 本地 Whisper 推理、实时示例 | 更换模型格式与推理运行时的成本较高；目前没有同机证据表明快于现用 faster-whisper。 |
| [Windows 实时字幕](https://support.microsoft.com/en-us/accessibility/windows/use-live-captions-to-better-understand-audio) | 系统声音字幕 | 官方实时翻译目标主要是英语和简体中文，不能直接完成本项目的德语目标字幕。 |

当前决定：保留已打包并经过测试的 PySide6、SoundCard、Silero VAD、faster-whisper 与本地 OPUS-MT 主链路。此前已加入输入状态提示并修复字幕恢复时限。本轮核对 LiveTranslate 的[默认设备轮询及重新连接逻辑](https://github.com/TheDeathDragon/LiveTranslate/blob/190ef027f38e577b0d2ecd06058ff6cbd236496b/audio_capture.py#L312-L334)后，在现有 SoundCard 采集器中独立实现约每 2 秒检查默认播放设备、旧设备断开时重开回采流，并在设置状态中显示当前设备名。短暂无默认设备或旧流先失效时最多重试 5 秒；每次重开录音流都会增加代次，即使设备 ID 没变也重置本地语音分段并重新建立 Azure 识别流，过滤前一代次晚到的字幕事件，避免跨流拼接语音。手动指定设备不自动跟随，设备不可用时明确报错，不回退到其他任意端点。没有复制其源码或安装其运行服务。

本机此前的视频端到端测试遇到 Windows 播放端点自身电平为零；这次设备跟随改动不等于已修复系统播放声。微软的 [WASAPI 回采文档](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)说明，回采读取所选播放端点的共享模式声音；独占模式和受保护内容存在限制。

下一轮若加入 OpenAI 兼容翻译提供者，应仅对稳定原文发起付费请求，设超时、队列上限和会话编号；先用真实语音样本与当前本地翻译做准确率、从发声到显示的延迟和费用对比，再作为可选模式发布。若移植 LiveTranslate 代码，发行目录必须保留其 MIT 许可文本。任何在线调用需由使用者显式启用并提供凭据。
