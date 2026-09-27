## 改了什么

<!-- 一两句话说清动机和做法；相关 Issue 用 “Closes #12” 关联。 -->

## 怎么验证的

<!-- 写清实际跑过的命令和结果。跳过（skip）的测试不算通过，请分开写。 -->

- [ ] `python -m pytest -q --cov=deutsch_overlay --cov-report=term`
- [ ] 涉及界面或回采时，按 README 的验证一节做过真实执行验证（本机 GPU / 播放设备 / Azure 是否用到：）

## 检查清单

- [ ] 一个 PR 只做一件事
- [ ] [CHANGELOG.md](https://github.com/aolingge/DeutschOverlay/blob/main/CHANGELOG.md) 已记录面向用户的改动
- [ ] 文档（README / SECURITY / THIRD-PARTY-NOTICES）随行为变化一起更新
- [ ] 没有提交密钥、录音、含用户名的完整路径，或 `models/`、`dist/`、`release/` 产物
- [ ] 没有新增默认联网或上传音频的行为；如必须联网，由用户主动开启
