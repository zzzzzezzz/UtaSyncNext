# 当前状态（2026-10-13）

## 已实现

- Next 独立仓库、V3 只读 guard 和单实例启动。
- 默认 `next` 工作台布局，经典布局仅通过 `UTASYNC_UI_VARIANT=classic` 作为兼容回退。
- 轻量 UI `.venv` 与重引擎 `.engine` 分层，模型独立下载/完整性检查。
- multiprocessing worker、取消和超时强杀、进度/日志/错误恢复报告。
- Faster-Whisper、可选 Demucs、歌词诊断/清理、OpenAI 兼容翻译、SRT/ASS 和 FFmpeg 压制。
- ASR v7 / 翻译 v8 缓存、批次翻译续跑、ASS 预览、字体导入和预设删除/改名/恢复。
- Launcher boundary、打包审计、回归工作区和大量本地 smoke。
- 第二次启动（源码运行或打包后的 exe）会把已运行的窗口带到前台，只有确实找不到窗口时才提示恢复方式。

## 仍在演进

- 串行批量任务队列、任务卡片、重试/恢复入口仍在 UI 重构计划中。
- 部分大型研究脚本和 `test_workspace` 证据属于研究/验收资产，不应直接当成生产算法契约。
- 真正 API 翻译、large-v2/large-v3 下载、长视频和 Demucs 耐久测试不属于默认验证。

## 当前限制

- 运行环境以 Windows、Python 3.10.11、可用 FFmpeg/FFprobe 为主；Linux 仅有文件锁回退，未作为发布目标。
- GPU/CUDA 依赖驱动和 DLL；ASR 可在运行时回退 CPU，但速度会下降。
- 没有 `.engine` 或完整模型时，UI 可启动，任务会在预检阶段停止。
- 没有 API Key 时只生成原文 SRT（仍可生成 ASS）；不会生成假翻译结果。
- `user_settings.json` 含本地配置和可能的明文 Key，分享项目或错误报告前必须移除。

历史计划文件描述过渡目标，不能用来判断上述功能是否已经落地；请以本目录 README、模块地图和代码为准。
