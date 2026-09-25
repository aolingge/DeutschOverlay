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

当前决定：保留已打包并经过测试的 PySide6、SoundCard、Silero VAD、faster-whisper 与本地 OPUS-MT 主链路。本轮吸收“让采音状态与字幕状态分开可见”的交互经验，加入输入状态提示，并修复字幕恢复时限。没有复制外部项目代码或安装其运行服务。

下一轮若加入 OpenAI 兼容翻译提供者，应仅对稳定原文发起付费请求，设超时、队列上限和会话编号；先用真实语音样本与当前本地翻译做准确率、从发声到显示的延迟和费用对比，再作为可选模式发布。若移植 LiveTranslate 代码，发行目录必须保留其 MIT 许可文本。任何在线调用需由使用者显式启用并提供凭据。
