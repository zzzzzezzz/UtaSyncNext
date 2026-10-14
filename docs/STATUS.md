# 当前状态（2026-10-14）

## 已实现

- Next 独立仓库、V3 只读 guard 和单实例启动。
- 默认 `next` 工作台布局，经典布局仅通过 `UTASYNC_UI_VARIANT=classic` 作为兼容回退。
- 轻量 UI `.venv` 与重引擎 `.engine` 分层，模型独立下载和完整性检查。
- multiprocessing worker、取消和超时强杀、进度/日志/错误恢复报告。
- Faster-Whisper、可选 Demucs、歌词诊断/清理、OpenAI 兼容翻译、SRT/ASS 和 FFmpeg 压制。
- ASR v7 / 翻译 v8 缓存、批次翻译续跑、ASS 预览、字体导入和预设删除/改名/恢复。
- Launcher boundary、打包审计、回归工作区和大量本地 smoke。
- 第二次启动（源码运行或打包后的 exe）会把已运行的窗口带到前台，只有确实找不到窗口时才提示恢复方式。
- 硬字幕压制按 NVENC → Intel QSV → AMD AMF → CPU 软压 逐级回退，日志会写明每一步的失败原因。
- 文档体系：README 加 `docs/` 16 篇，以及 GitHub Actions 上的 `verify` 工作流。

## 仍在演进

- 批量任务队列已决策不做（每个视频的花字和预设都要逐片调整，队列会牺牲这种逐片控制）；任务卡片、重试/恢复入口仍在 UI 重构计划中。
- 部分大型研究脚本和 `test_workspace` 证据属于研究/验收资产，不应直接当成生产算法契约。
- 真正 API 翻译、large-v2/large-v3 下载、长视频和 Demucs 耐久测试不属于默认验证。

## 发布（杀青）前还剩的事

当前没有待做的性能重构。缓存正确性修复和本地 health 闸门已经完成；发布前按 [`PERFORMANCE_TESTING.md`](PERFORMANCE_TESTING.md) 分级处理：

- **P0**：用最新源代码重建 Launcher，跑打包边界/体积/启动 smoke、`quick`、密钥扫描和 V3 边界检查。
- **P1**：有条件时做一次真实 API 人工检查、干净环境首启、另一台 Windows 成品短任务，以及取消/恢复抽查。
- **P2**：固定性能基线、真实 Intel/AMD 编码、长 Demucs 耐久、动态线程和 GPU 峰值监控，发布后继续做，不阻塞当前入库。

需要你拍板的三件事：

1. 公开前是否把提交邮箱改成 noreply 地址。
2. 是否加 `LICENSE`（MIT、Apache-2.0，或不加）。
3. 推到哪个远端（GitHub、Gitee 或自建 GitLab）；Next 目前没有配置任何 remote。

密钥与隐私的完整复核见 [`SECURITY.md`](SECURITY.md)；发布前要在最终提交之后重新运行扫描。`test_workspace` 证据里含本机绝对路径，属于可选的清理项。

## 当前限制

- 运行环境以 Windows、Python 3.10.11、可用 FFmpeg/FFprobe 为主；Linux 仅有文件锁回退，未作为发布目标。
- GPU/CUDA 依赖驱动和 DLL；ASR 可在运行时回退 CPU，但速度会下降。
- 没有 `.engine` 或完整模型时，UI 可启动，任务会在预检阶段停止。
- 没有 API Key 时只生成原文 SRT（仍可生成 ASS）；不会生成假翻译结果。
- `user_settings.json` 含本地配置和可能的明文 Key，分享项目或错误报告前必须移除。

历史计划文件描述过渡目标，不能用来判断上述功能是否已经落地；请以本目录 README、模块地图和代码为准。
