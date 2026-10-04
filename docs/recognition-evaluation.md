# 识别准确性与时间戳评估（2026-10-04）

本轮完成固定样本评估工具与真实推理验证。新增 `--input-rates`、替换/删除/插入计数、整段无输出计数和有效逐词时间戳文本覆盖率。缺少人工逐词标注时，覆盖率不能当成时间对齐准确率。新增 7 个指标回归测试。

## 样本与实验方法

- 初始固定清单共 51 项：ASCEND 测试集第一批符合时长 2–20 秒的英文、中文、中英混合片段各 8 条；此前 FLEURS 固定清单每语言前 3 条，共 9 条；每条 FLEURS 各加一种真实噪声、一种音乐，共 18 个混合版本。
- 发现初始 ASCEND 清单仅包含一位说话者后，另行固定第二位测试说话者各语言前 4 条，先写选择策略再推理。最终 Small/Turbo 对比共 63 项、36 条对话，来自 2 位说话者、2 个会话。没有按模型结果挑选样本。
- 18 个混合版本是合成压力测试，不能当作 18 条独立人类录音。9 条 FLEURS 也曾参与之前评估，不是新的盲测集。ASCEND 是本工作未用于训练的测试数据，无法保证 Whisper 预训练未接触。
- Both models: CUDA `int8_float16`、beam 3、明确语言、逐词时间、简体输出，48 kHz 音频输入按现有重采样器转至 16 kHz。中英混合指定中文，使用包含英文字符的 CER，并非 MER。
- 全程离线解码，不播放录音，不修改系统音量，不使用账号凭据。额外安装 `pyarrow 25.0.1` 到项目已有 `.venv`，仅用于解析数据文件，没有运行远端数据集代码。

## 实测结果

错误率越低越好；以下对话结果各有 12 个片段。

| 组别 | Small | Turbo | 参考单位 |
|---|---:|---:|---:|
| 英语对话 WER | 35.64%（36） | 33.66%（34） | 101 词 |
| 中文对话 CER | 20.25%（33） | 14.72%（24） | 163 字符 |
| 中英混合对话 CER | 25.68%（76） | 20.27%（60） | 296 字符 |
| 德语 + 噪声 WER | 3.23%（2） | 1.61%（1） | 62 词 |
| 德语 + 音乐 WER | 6.45%（4） | 3.23%（2） | 62 词 |
| 英语 + 噪声 WER | 9.59%（7） | 6.85%（5） | 73 词 |
| 英语 + 音乐 WER | 12.33%（9） | 8.22%（6） | 73 词 |
| 中文 + 噪声 CER | 12.35%（10） | 3.70%（3） | 81 字符 |
| 中文 + 音乐 CER | 11.11%（9） | 3.70%（3） | 81 字符 |

Turbo 各组总错误率更低或持平，但有 5/63 个单项变差。英语对话删除错误由 16 增至 19，说明总 WER 下降不代表漏词必然减少。中文删除由 17 降至 14，混合由 45 降至 40。两个模型均无整段空输出；不能据此断言没有漏句。

Turbo 混合语言的有效逐词文本覆盖率为 95.82%，其余组为 100%；Small 各组为 98.39%–100%。没有计入零时长或逆序词；没有人工逐词起止标准，不能给出毫秒准确率或字幕同步准确率。

## 术语消融

仅对原始 51 项比较 Turbo 空术语与固定术语表。相同参数下 2 项错误率改善、2 项变差，其余持平。因此术语应默认空，仅按实际视频填写，不预装一组“万能术语”。

德语四个参考术语 Hongkong、Kowloon、Dinosaurier、Unabhängigkeitserklärung，在原音/噪声/音乐三个版本中，空术语和有术语均命中 12/12。没有观察到这些词的额外收益。英文和中文术语在所选参考文本中不存在，因此只是无关术语干扰对照，不能据此证明对应语言的术语召回改善。

## 来源、许可和证据

- [ASCEND 官方数据集](https://huggingface.co/datasets/CAiRE/ASCEND)，CC-BY-SA-4.0，CAiRE / Lovenia et al.；revision `737e9800ae31be9932ba8464c80366559bd28424`。测试 Parquet SHA256 `a4c81d2b5ed6124f052089a695972808c16e0ce0c365ec9773c5d1a8fcf043a7`。仅下载约 106 MB 测试文件，读取选中音频；未发布素材。
- [FLEURS 官方数据集](https://huggingface.co/datasets/google/fleurs)，CC-BY-4.0，Google / Conneau et al.；revision `ab93cf03f9d0cd083c853fad065a6377067408aa`。
- [MUSAN 官方项目](https://www.openslr.org/17/)；使用 [FluidInference 镜像](https://huggingface.co/datasets/FluidInference/musan/tree/3edcfdf89b56dbe6a395ff29f9c29489e03d1321) 中 `noise/free-sound/noise-free-sound-0000.wav`，该子目录 LICENSE 标为 Public Domain。保留 LICENSE/ANNOTATIONS 原文，不将未知具体声源描述为特定交通或人群噪声。
- 音乐使用 Kevin MacLeod 的 **Slow Burn**，[原作者下载页](https://incompetech.com/music/royalty-free/)。MUSAN `music/rfm/LICENSE` 标明曲目 `music-rfm-0120` 为 [CC-BY-3.0](https://creativecommons.org/licenses/by/3.0/)；已保存许可原文。实际音频直接从原作者下载，非 MUSAN 版本，SHA256 记录在清单。噪声按全文件 RMS 混至 5 dB，音乐 10 dB，从首样本开始循环至语音长度，两路共同峰值缩放避免削波。
- [Mozilla 德语自然语音候选](https://datacollective.mozillafoundation.org/datasets/cmj8u489v001tnxzp7x8ayacr)只读取公开落地页，未通过账号下载。当前仍缺德语真实对话和更多说话者覆盖，不应将本结果泛化到所有口音。

证据目录：`E:\codemain\others\output\yt-dual-subs-completion-20261004\accuracy`。选择策略、下载/混音脚本、清单、逐项结果、`summary.json` 和许可均在此。`small.json`/`turbo.json` 是初始 51 项，`*-diversity.json` 是追加 12 项，术语结果只有初始 51 项；不得直接比较不同样本量的总数。

复现：运行目录内 `fetch-conversation.py`、`prepare-stress.py`、`expand-speakers.py`，再以现有 `scripts/benchmark-recognition.py` 分别运行两个清单和本地模型，最后运行 `summarize.py`。下载需要 requests、pyarrow；推理只读取本地模型。脚本不进行播放。示例：

```powershell
.venv\Scripts\python.exe scripts\benchmark-recognition.py <manifest.json> --output <results.json> --device cuda --chinese-script simplified --input-rates 48000 --model-path <existing-model-directory>
```

这轮未做翻译质量人工评分、强噪声范围扫描、人工词时间对齐或端到端延迟比较。不得用整文件解码耗时代表实时字幕延迟。相应实时验证由浏览器与桥接测试单独给出。
