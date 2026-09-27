# 参与开发 / Contributing

欢迎提交 Issue 和 Pull Request。项目只支持 **Windows 11 x64 + Python 3.12**，并把“本地优先、离线可用”当作前提：任何需要联网或上传音频的能力，都必须由用户在设置里主动开启后才生效。

## 开发环境

```powershell
git clone https://github.com/aolingge/DeutschOverlay.git
cd DeutschOverlay
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[local,gpu,online,dev]'
$env:DEUTSCH_OVERLAY_MODELS = (Join-Path (Get-Location) 'models')
.\.venv\Scripts\python.exe -m deutsch_overlay.app
```

`models/` 不随仓库分发。按 [MODEL_SOURCES.md](MODEL_SOURCES.md) 里的来源准备模型，再用 `scripts/check_models.py` 校验：

```powershell
.\.venv\Scripts\python.exe scripts\check_models.py models
```

## 修改代码前

- 先跑一遍全量测试，确认起点是干净的：`python -m pytest -q --cov=deutsch_overlay --cov-report=term`。
- 依赖设备、模型或 Azure 的测试在缺少条件时会跳过（例如 GPU 需要 `nvidia.cudnn`，真实模型需要 `DEUTSCH_TEST_REAL_MODELS=1`）。**跳过不等于通过**，请在 PR 里写清哪些是跳过、哪些是真跑过。
- 字幕相关的改动请同时考虑：本地模式、在线模式、GPU 解码失败回退、显示器变化后的重新定位。

## 提交 PR

1. 一个 PR 只解决一件事，标题写清“改了什么”。
2. 运行全量测试；涉及界面或回采的改动，按 [README](README.md#验证) 里的命令做一次真实执行验证。
3. 在 [CHANGELOG.md](CHANGELOG.md) 里记录面向用户的改动。
4. 确认没有提交密钥、录屏录音、包含用户名的完整路径或 `models/`、`dist/`、`release/` 里的产物。

## 报告问题

- 功能异常请用 [Issue 模板](https://github.com/aolingge/DeutschOverlay/issues/new/choose)，附上 Windows 版本、显卡与显存、复现步骤、设置窗口状态栏的文字。
- **不要**在公开 Issue 里粘贴 Azure 密钥、录音文件，或含用户名的完整安装路径。
- 安全与隐私问题按 [SECURITY.md](SECURITY.md) 私下报告。

## 语言

文档、提交信息和代码注释以中文为主；代码标识符和文件名用英文。In English: keep documentation Chinese-first, use English identifiers, and describe your change in the pull request form.
