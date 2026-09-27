# 安全与隐私 / Security and privacy

## 这个应用会接触什么数据

- **默认（本地模式）**：播放设备的回采音频只在内存中用于语音分段和本地识别；**不写磁盘、不上传、不保存录音**。学习记录只存在于本次运行的内存里，退出即消失。
- **在线模式（需用户主动开启）**：只有当你在“在线服务”页保存凭据并手动选择“在线 · Azure”后，播放声才会发送到 Microsoft Azure Speech。此刻音频和识别结果受 [Azure 隐私条款](https://privacy.microsoft.com/privacystatement)约束，并可能产生费用。
- **凭据**：Azure 区域与密钥通过 `keyring` 存入 **Windows 凭据管理器**（`DeutschOverlay.AzureSpeech`），不写入设置文件、不写入日志、不上传。旧版本遗留的 `region` / `key` 条目不会被自动删除，需要你自行在凭据管理器中清理。
- **无遥测**：应用不收集使用统计，不发起除 Azure 与模型下载以外的网络请求。

## 报告漏洞

请通过 GitHub 的 [Security → Report a vulnerability](https://github.com/aolingge/DeutschOverlay/security/advisories/new) 私下报告，或在 [Issues](https://github.com/aolingge/DeutschOverlay/issues) 提交（请勿附带真实密钥）。通常会在若干天内回复。

提交问题时请**不要**粘贴：Azure 订阅密钥、完整安装路径中的用户名、录音文件。日志可用 `--self-test` / `--audio-self-test` 的退出码代替。

## 密钥泄露的处置

若密钥曾出现在截图、日志或提交中：

1. 立刻在 Azure 门户**重新生成密钥**（旧密钥立即失效）；
2. 在 Windows 凭据管理器中删除 `DeutschOverlay.AzureSpeech` 下的旧条目；
3. 若已进入 Git 历史，请同时清理历史并强制推送——仅仅删除文件不能移除历史中的明文。
