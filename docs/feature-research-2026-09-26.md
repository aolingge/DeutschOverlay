# 同类字幕工具与下一步取舍（2026-09-26）

本轮只依据各项目自身仓库或微软官方文档核对功能。没有下载或复制第三方实现代码。

| 来源 | 已核实能力 | 对本项目的决定 |
| --- | --- | --- |
| [Microsoft Windows Live Captions](https://support.microsoft.com/en-us/accessibility/windows/use-live-captions-to-better-understand-audio) | 全系统声音字幕、样式个性化；Copilot+ 设备的翻译目标是英语或简体中文。 | 保持德语目标语言、可拖动字幕和中德/英德双语作为本应用核心。 |
| [Accessible Live Captions](https://github.com/kellylford/LiveCaptionsWithAccessibility) | 可滚动回看、复制/保存字幕；还能选系统音频或单个程序。 | 吸收“字幕可回看”的交互，在本应用加入本次运行内存记录、复制与清空；不复制其代码。 |
| [LiveTranslate](https://github.com/TheDeathDragon/LiveTranslate) | 多识别后端、主题、速度对比和可选麦克风混音。 | 现有应用已有本地/在线模式及可自定义背景；当前先验证单一核心识别链路，不增加尚未实测的模型管理器。 |
| [微软按进程环回音频样例](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/) | 可只采集指定进程及子进程，或从系统混音排除指定进程；与单一播放端点不同。 | 值得用于“只听游戏，不听通知/音乐”，但需要原生 Windows 组件、设备异常处理与游戏实测，当前版本不宣称已支持。 |

本轮落地的是可回看的德语学习记录。按程序采音和其它识别后端须先有独立的兼容性与准确率证据，再加入可安装成品。
