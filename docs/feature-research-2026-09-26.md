# 同类字幕工具与下一步取舍（2026-09-26）

本轮只依据各项目自身仓库或微软官方文档核对功能。没有下载或复制第三方实现代码。

| 来源 | 已核实能力 | 对本项目的决定 |
| --- | --- | --- |
| [Microsoft Windows Live Captions](https://support.microsoft.com/en-us/accessibility/windows/use-live-captions-to-better-understand-audio) | 全系统声音字幕、样式个性化；Copilot+ 设备的翻译目标是英语或简体中文。 | 保持德语目标语言、可拖动字幕和中德/英德双语作为本应用核心。 |
| [Accessible Live Captions](https://github.com/kellylford/LiveCaptionsWithAccessibility) | 可滚动回看、复制/保存字幕；还能选系统音频或单个程序。 | 吸收“字幕可回看”的交互，在本应用加入本次运行内存记录、复制与清空；不复制其代码。 |
| [LiveTranslate](https://github.com/TheDeathDragon/LiveTranslate) | 多识别后端、主题、速度对比和可选麦克风混音。 | 现有应用已有本地/在线模式及可自定义背景；当前先验证单一核心识别链路，不增加尚未实测的模型管理器。 |
| [微软按进程环回音频样例](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/) | 可只采集指定进程及子进程，或从系统混音排除指定进程；与单一播放端点不同。 | 值得用于“只听游戏，不听通知/音乐”，但需要原生 Windows 组件、设备异常处理与游戏实测，当前版本不宣称已支持。 |

本轮落地的是可回看的德语学习记录。按程序采音和其它识别后端须先有独立的兼容性与准确率证据，再加入可安装成品。

## 2026-09-27 官方资料复核与本轮整合

| 官方资料 | 对本应用的处理 |
| --- | --- |
| [Azure Python 连续识别 API](https://learn.microsoft.com/en-us/python/api/azure-cognitiveservices-speech/azure.cognitiveservices.speech.recognizer) 与 [.NET 停止识别说明](https://learn.microsoft.com/en-us/dotnet/api/microsoft.cognitiveservices.speech.speechrecognizer.stopcontinuousrecognitionasync) | Python SDK 用异步停止和事件回调；.NET 文档明确说明最终结果可能晚于停止任务完成。跨 SDK 行为需实网复核，因此本应用对**静音自动停流**保留至多 2 秒 SDK 停止等待和 1 秒会话结束等待，期间接收最后的识别结果；用户主动暂停或退出仍立即丢弃旧结果。已用模拟回调测试，不宣称真实 Azure 网络验收。 |
| [Qt 无障碍名称](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QWidget.html) 与 [QLabel buddy](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QLabel.html) | 设置页补充控件名称和表单标签关联，颜色按钮名称随颜色更新；外部设备名写入的状态文字强制按纯文本显示。 |
| [Azure Speech 官方价格页](https://azure.microsoft.com/en-us/pricing/details/speech/) | Speech Translation F0 显示每月 5 小时免费额度；实际付费价格随区域、账户、币种和日期变化。README 移除固定美元单价，保留实时价格页和账单设置提醒。 |
| [微软按进程环回音频样例](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/) | 官方 API 可按进程树筛选，并不绑定单个端点，要求 Windows 10 Build 20348 或更新版本。此能力仍需原生组件及实际游戏验证，当前不并入默认采音链路。 |

本轮未增加依赖、后台服务或收费识别调用。采用先修复可复现可靠性问题的做法：GPU 解码期失败回退 CPU、在线静音停流保留最后结果、最终字幕不被晚到的临时字幕覆盖，并调整空记录复制和设置反馈。未来要比较新识别后端，应先用相同真人语音、同一硬件测准确率、端到端延迟和包体积，再决定是否替换已验证的本地链路。
