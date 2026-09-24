# 本地模型来源与署名

本应用将模型文件放在 EXE 旁边的 `models` 文件夹。每个模型的 `source.json` 记录上游仓库和转换时使用的具体提交版本。

| 目录 | 上游模型 | 模型许可 |
| --- | --- | --- |
| `whisper-small` | [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small)，由 OpenAI Whisper small 转换 | MIT |
| `opus-en-de` | [Helsinki-NLP/opus-mt-en-de](https://huggingface.co/Helsinki-NLP/opus-mt-en-de) | CC BY 4.0 |
| `opus-zh-de` | [Helsinki-NLP/opus-mt-zh-de](https://huggingface.co/Helsinki-NLP/opus-mt-zh-de) | Apache 2.0 |

模型提供方、许可和限制以各上游模型卡为准。英文、中文到德语的模型文件由原始模型转换为 CTranslate2 格式，翻译内容应结合上下文核对。
