# UtaSyncNext

UtaSyncNext 是 UtaSync 的新一代 Windows 桌面版：Python + CustomTkinter 的本地字幕工作台。它不是 Next.js、React 或 Web 服务项目，仓库中没有 `package.json`、npm 脚本、HTTP 路由或登录服务。实际开发入口是 `app.py`，实际长任务入口是 `engine.pipeline_worker.run_pipeline_worker`。

这份 README 是软件使用者的快速入口，也给维护者提供当前事实基线。Trae、Cursor 等代码工具请从 [`docs/README.md`](docs/README.md) 和 [`docs/AI_HANDOFF.md`](docs/AI_HANDOFF.md) 进入；历史路线图和父目录 V3 说明仅作参考，若与代码冲突，以 `UtaSyncNext` 当前源代码和测试为准。

## 先跑起来

### 克隆之后

```powershell
git clone <仓库地址> UtaSyncNext
Set-Location UtaSyncNext
git config core.hooksPath .githooks        # 启用提交前的密钥和 V3 检查，只需设置一次
powershell -ExecutionPolicy Bypass -File tools\bootstrap_venv.ps1
```

离开原来那台机器时，`fonts/`、`user_settings.json`、`.engine`、`models_faster` 都不在 Git 里：
前两个缺失时预设会回退到 Windows 自带字体，后两个缺失时任务会在预检阶段停下并提示安装方式。
检出旁边没有只读 V3 参考目录时，`tools/v3_guard.py` 会打印跳过说明并以 0 退出，不会挡住提交。

### 在这台开发机上

在 Windows PowerShell 中进入 Next 独立仓库：

```powershell
Set-Location E:\traecode\UtaSync\UtaSyncNext
.\.venv\Scripts\python.exe tools\v3_guard.py doctor
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

`run_next.ps1`/`.cmd` 会使用 `.venv\Scripts\python.exe`，将 HuggingFace 和 XDG 缓存重定向到 Next 自己的 `.cache`。直接运行 `.\.venv\Scripts\python.exe app.py` 也可以；入口会再次应用同一缓存策略。

最小本地验证：

```powershell
.\.venv\Scripts\python.exe tools\verify_next_stack.py --profile health --json-report test_logs\health.json
.\.venv\Scripts\python.exe tools\verify_next_stack.py --profile quick
```

`health` 仍会运行大量本地 smoke、Tk 预览和回归检查；它不是只检查 Python 包的秒级检查。它跳过双语/纯中文假 API translator smoke 和 UI import 检查，后两项由 `quick` 补充。详见 [`TEST_PLAN.md`](TEST_PLAN.md) 和 [`docs/TESTING.md`](docs/TESTING.md)。

## 当前能力

- 导入视频、音频和参考歌词，支持文件选择及拖拽。
- 本地 Faster-Whisper 打轴；可选 Demucs 人声分离。
- 可选 OpenAI 兼容接口翻译，支持中日双语或纯中文字幕。
- SRT、ASS 花字、缓存复用、错误日志和恢复报告。
- ASS 预览、字体导入/搭配、预设编辑、字幕浏览器校对。
- FFmpeg 硬字幕压制：按 `h264_nvenc`、`h264_qsv`、`h264_amf`、`libx264` 的顺序逐个尝试，前三个用不了就落到 CPU 软压。
- 轻量 Launcher 与 `.engine`、`models_faster` 分离，模型不随 Launcher 打包。

默认任务会跳过 Demucs，以尽快得到打轴结果；在设置中选择高质量人声分离后才会检查 Demucs 权重。任务只使用本地已完整下载的 Faster-Whisper 模型，不会在管线中偷偷联网下载。

## 代码和文档导航

不要从这里逐篇翻阅 `docs/`。先打开 [`docs/README.md`](docs/README.md)，它按软件使用、二次开发、验证发布和 Trae/Cursor 交接分组，并给出每类的最短阅读路径。

普通使用者主要需要：本页的启动说明、[`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) 的排障说明，以及软件内的设置和输出提示。维护者需要 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)、[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)、[`docs/PIPELINE.md`](docs/PIPELINE.md) 和 [`docs/TESTING.md`](docs/TESTING.md)。发布前再看 [`docs/PACKAGING.md`](docs/PACKAGING.md)、[`docs/SECURITY.md`](docs/SECURITY.md)、[`docs/STATUS.md`](docs/STATUS.md) 和 [`docs/PERFORMANCE_TESTING.md`](docs/PERFORMANCE_TESTING.md)。

[`AGENTS.md`](AGENTS.md) 和 [`SAFETY.md`](SAFETY.md) 是仓库边界规则；[`docs/AI_HANDOFF.md`](docs/AI_HANDOFF.md) 是给 Trae/Cursor 复制使用的短入口。

## 目录边界

`UtaSyncNext` 是独立 Git 仓库；父目录 `E:\traecode\UtaSync` 是冻结的 V3 只读参考。不要在父目录执行写入、格式化、测试、打包、`git add` 或 `git commit`。Next 的本地运行资产包括 `.venv`、`.engine`、`models_faster`、`.cache`、`output`、`fonts`、`test_logs` 和 `user_settings.json`，这些都不应提交；密钥只应保存在被 Git 忽略的 `user_settings.json` 中。推送前的密钥与隐私复核见 [`docs/SECURITY.md`](docs/SECURITY.md)。默认 UI 由 `MainWindow` 固定使用 Next 工作台，`UTASYNC_UI_VARIANT=classic` 才切换兼容布局。

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

复核日期：2026-10-14。
