# UtaSyncNext

UtaSyncNext 是 UtaSync 的新一代 Windows 桌面版：Python + CustomTkinter 的本地字幕工作台。它不是 Next.js、React 或 Web 服务项目，仓库中没有 `package.json`、npm 脚本、HTTP 路由或登录服务。实际开发入口是 `app.py`，实际长任务入口是 `engine.pipeline_worker.run_pipeline_worker`。

这份 README 是给维护者以及 Trae、Cursor 等代码工具的当前事实入口。历史路线图和父目录 V3 说明仍有参考价值，但若与代码冲突，以 `UtaSyncNext` 当前源代码和测试为准。

## 先跑起来

在 Windows PowerShell 中进入 Next 独立仓库：

```powershell
Set-Location E:\traecode\UtaSync\UtaSyncNext
\.venv\Scripts\python.exe tools\v3_guard.py doctor
```

首次安装轻量 UI 环境；需要真正执行 ASR/Demucs 时，再安装外置重引擎：

```powershell
powershell -ExecutionPolicy Bypass -File tools\bootstrap_venv.ps1
powershell -ExecutionPolicy Bypass -File tools\bootstrap_venv.ps1 -Engine
```

确认 `ffmpeg` 和 `ffprobe` 已安装并在 `PATH` 后运行：

```powershell
.\run_next.ps1
# 或
.\run_next.cmd
```

`run_next.ps1`/`.cmd` 会使用 `.venv\Scripts\python.exe`，将 HuggingFace 和 XDG 缓存重定向到 Next 自己的 `.cache`。直接运行 `\.venv\Scripts\python.exe app.py` 也可以；入口会再次应用同一缓存策略。

最小本地验证：

```powershell
.\.venv\Scripts\python.exe tools\verify_next_stack.py --profile health --json-report test_logs\health.json
.\.venv\Scripts\python.exe tools\verify_next_stack.py --profile quick
```

`health` 仍会运行大量本地 smoke、Tk 预览和回归检查；它不是只检查 Python 包的秒级检查。它跳过双语/纯中文真实翻译 smoke 和 UI import 检查。详见 [`TEST_PLAN.md`](TEST_PLAN.md) 和 [`docs/TESTING.md`](docs/TESTING.md)。

## 当前能力

- 导入视频、音频和参考歌词，支持文件选择及拖拽。
- 本地 Faster-Whisper 打轴；可选 Demucs 人声分离。
- 可选 OpenAI 兼容接口翻译，支持中日双语或纯中文字幕。
- SRT、ASS 花字、缓存复用、错误日志和恢复报告。
- ASS 预览、字体导入/搭配、预设编辑、字幕浏览器校对。
- FFmpeg 硬字幕压制：优先 `h264_nvenc`，失败后回退 `libx264`。
- 轻量 Launcher 与 `.engine`、`models_faster` 分离，模型不随 Launcher 打包。

默认任务会跳过 Demucs，以尽快得到打轴结果；在设置中选择高质量人声分离后才会检查 Demucs 权重。任务只使用本地已完整下载的 Faster-Whisper 模型，不会在管线中偷偷联网下载。

## 代码和文档导航

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)：启动链路、进程/线程边界、消息协议和状态机。
- [`docs/MODULE_MAP.md`](docs/MODULE_MAP.md)：当前目录和模块职责索引。
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)：安装、调试、修改和提交流程。
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md)：设置键、默认值、兼容别名和运行目录。
- [`docs/PIPELINE.md`](docs/PIPELINE.md)：ASR、翻译、ASS、压制、缓存和输出契约。
- [`docs/TESTING.md`](docs/TESTING.md)：验证命令矩阵及联网/大模型风险边界。
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)：常见启动、模型、FFmpeg、CUDA、代理和缓存问题。
- [`docs/PACKAGING.md`](docs/PACKAGING.md)：Launcher 构建和发布边界。
- [`docs/EXTENDING.md`](docs/EXTENDING.md)：新增设置、UI、协议、模型和 smoke 的方式。
- [`docs/AI_HANDOFF.md`](docs/AI_HANDOFF.md)：可复制给 Trae/Cursor 的交接提示词。
- [`docs/DECISIONS.md`](docs/DECISIONS.md)：已落地的架构决策和不能随意回退的原因。
- [`docs/STATUS.md`](docs/STATUS.md)：当前实现、进行中事项和已知限制。
- [`AGENTS.md`](AGENTS.md)、[`SAFETY.md`](SAFETY.md)：V3 只读边界和安全检查，优先级高于一般开发习惯。

## 目录边界

`UtaSyncNext` 是独立 Git 仓库；父目录 `E:\traecode\UtaSync` 是冻结的 V3 只读参考。不要在父目录执行写入、格式化、测试、打包、`git add` 或 `git commit`。Next 的本地运行资产包括 `.venv`、`.engine`、`models_faster`、`.cache`、`output`、`fonts`、`test_logs` 和 `user_settings.json`，这些都不应提交；密钥只应保存在被 Git 忽略的 `user_settings.json` 中。默认 UI 由 `MainWindow` 固定使用 Next 工作台，`UTASYNC_UI_VARIANT=classic` 才切换兼容布局。

开始工作前运行：

```powershell
.\.venv\Scripts\python.exe tools\v3_guard.py begin
```

完成修改和验证后运行：

```powershell
.\.venv\Scripts\python.exe tools\check_no_secrets.py
.\.venv\Scripts\python.exe tools\v3_guard.py verify
```

## 当前实现与历史文档的关系

父目录的《UtaSyncNext 需求与重构计划.md》描述过渡路线，父目录的《UtaSync 架构白皮书与重构指引.md》主要描述 V3。它们不应被当成当前 Next 的模块树：当前管线在 `engine/pipeline_worker.py`，`engine/worker_entry.py` 只有轻量 demo 入口；规划中的 `core/pipeline_runner.py`、`utils/hash_manager.py` 和 `utils/paths.py` 当前不存在。

复核日期：2026-10-13。
