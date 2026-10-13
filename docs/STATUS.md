# 当前状态（2026-10-13）

## 已实现

- Next 独立仓库、V3 只读 guard 和单实例启动。
- 默认 `next` 工作台布局，经典布局仅通过 `UTASYNC_UI_VARIANT=classic` 作为兼容回退。
- 轻量 UI `.venv` 与重引擎 `.engine` 分层，模型独立下载和完整性检查。
- multiprocessing worker、取消和超时强杀、进度/日志/错误恢复报告。
- Faster-Whisper、可选 Demucs、歌词诊断/清理、OpenAI 兼容翻译、SRT/ASS 和 FFmpeg 压制。
- ASR v7 / 翻译 v8 缓存、批次翻译续跑、ASS 预览、字体导入和预设删除/改名/恢复。
- Launcher boundary、打包审计、回归工作区和大量本地 smoke。
- 第二次启动（源码运行或打包后的 exe）会把已运行的窗口带到前台，只有确实找不到窗口时才提示恢复方式。
- 文档体系：README 加 `docs/` 15 篇，以及 GitHub Actions 上的 `verify` 工作流。

## 仍在演进

- 批量任务队列已决策不做（每个视频的花字和预设都要逐片调整，队列会牺牲这种逐片控制）；任务卡片、重试/恢复入口仍在 UI 重构计划中。
- 部分大型研究脚本和 `test_workspace` 证据属于研究/验收资产，不应直接当成生产算法契约。
- 硬件压制目前只有 NVENC 和 libx264 两个候选，V3 时代的 QSV/AMF 没有恢复，详见 [`V3_COMPARISON.md`](V3_COMPARISON.md)。
- 真正 API 翻译、large-v2/large-v3 下载、长视频和 Demucs 耐久测试不属于默认验证。

## 发布（杀青）前还剩的事

属于"发布前做一次"、不必进默认 CI 的验证，清单和跑法在 [`PERFORMANCE_TESTING.md`](PERFORMANCE_TESTING.md)：

- 真实 API 翻译跑通双语和纯中文，并人工检查断句与文学质量。
- 干净机器首启：没有 `.engine`、没有模型、没有 `fonts/`、没有 `user_settings.json`。
- 长视频加 Demucs 的耐久验证。
- 打包成品拷到另一台机器双击验证。
- 记录第一份性能基线。

需要你拍板的三件事：

1. 公开前是否把提交邮箱改成 noreply 地址。
2. 是否加 `LICENSE`（MIT、Apache-2.0，或不加）。
3. 推到哪个远端（GitHub、Gitee 或自建 GitLab）；Next 目前没有配置任何 remote。

密钥与隐私的完整复核见 [`SECURITY.md`](SECURITY.md)：已扫描 212 个已跟踪文件和全部 29 个提交，没有真实密钥。`test_workspace` 证据里含本机绝对路径，属于可选的清理项。

## 当前限制

- 运行环境以 Windows、Python 3.10.11、可用 FFmpeg/FFprobe 为主；Linux 仅有文件锁回退，未作为发布目标。
- GPU/CUDA 依赖驱动和 DLL；ASR 可在运行时回退 CPU，但速度会下降。
- 没有 `.engine` 或完整模型时，UI 可启动，任务会在预检阶段停止。
- 没有 API Key 时只生成原文 SRT（仍可生成 ASS）；不会生成假翻译结果。
- `user_settings.json` 含本地配置和可能的明文 Key，分享项目或错误报告前必须移除。

历史计划文件描述过渡目标，不能用来判断上述功能是否已经落地；请以本目录 README、模块地图和代码为准。