# UtaSyncNext 二次开发指南（Trae/Cursor）

复核日期：2026-10-13。本文面向需要接手代码的开发者和 AI 编程工具。

## 工作区边界

请只把 `E:\traecode\UtaSync\UtaSyncNext` 打开为 Trae/Cursor 工作区。父目录 `E:\traecode\UtaSync` 是 UtaSync v3，只能只读参考；不要在父目录运行写入、格式化、安装、测试、提交或清理命令，也不要把 Next 改动复制回 v3。详细边界见 [`AGENTS.md`](../AGENTS.md) 和 [`SAFETY.md`](../SAFETY.md)。

这不是 Next.js 项目：不要运行 `npm install`、`npm run dev` 或寻找 Web 路由。入口是 `app.py`，GUI 是 CustomTkinter，依赖清单是 `requirements-*.txt`。

## Python 与环境分层

当前 `.venv/pyvenv.cfg` 和 `.engine/pyvenv.cfg` 都基于 Python **3.10.11**（`E:\Python310`）。两层目的不同：

- `.venv/`：轻量 UI、模型下载/管理、工具和打包环境；`requirements-ui.txt` 包含 CustomTkinter、Pillow、OpenAI、Requests、HuggingFace Hub 等。
- `.engine/`：重型 AI 依赖；`requirements-engine.txt` 包含 Faster-Whisper、CTranslate2、Demucs、OpenAI、Torch 2.5.1+cu121、Torchaudio 2.5.1+cu121；GPU 核心补充见 `requirements-engine-gpu-core.txt`。

真实管线由 `.venv\Scripts\python.exe` 创建子进程，再把 `.engine\Lib\site-packages` 挂入该子进程。`engine.locator.EngineLocation.python` 用于工具/Demucs 子命令和诊断，不代表管线通过另一个解释器启动。除 Demucs 子命令外，不要擅自把 worker 改成调用 `.engine\Scripts\python.exe app.py`。

## 首次安装与启动

在 PowerShell 中：

```powershell
Set-Location E:\traecode\UtaSync\UtaSyncNext

# 仅安装可启动 GUI 的轻环境
powershell -ExecutionPolicy Bypass -File .\tools\bootstrap_venv.ps1

# 需要真实 ASR/Demucs 管线时，再安装重环境（会下载较大依赖）
powershell -ExecutionPolicy Bypass -File .\tools\bootstrap_venv.ps1 -Engine

# 通过本机代理安装，例如 10808 或 127.0.0.1:10808
# powershell -ExecutionPolicy Bypass -File .\tools\bootstrap_venv.ps1 -Engine -Proxy 10808

# 启动 GUI；run_next.ps1/cmd 会设置 Next 本地 HF 缓存
powershell -ExecutionPolicy Bypass -File .\run_next.ps1
```

没有 `.venv` 时，`run_next.ps1`/`run_next.cmd` 会提示运行 bootstrap；GUI 可以没有 `.engine` 打开，但开始真实任务前会阻止并提示 `-Engine`。FFmpeg 和 FFprobe 必须在 `PATH` 中。

可选的引擎健康检查：

```powershell
.\.venv\Scripts\python.exe tools\check_engine_runtime.py
# --deep 才导入 Torch 并查询 torch.cuda；不要在普通 UI 快测中随意加上
.\.venv\Scripts\python.exe tools\check_engine_runtime.py --deep
```

## 开发一个功能时的顺序

1. 先阅读 [`MODULE_MAP.md`](./MODULE_MAP.md)、目标模块及其 smoke；确认改动属于 UI、controller、worker、core、utils 还是 packaging。
2. 开始修改前运行边界快照：

   ```powershell
   .\.venv\Scripts\python.exe tools\v3_guard.py begin
   ```

3. 保持 UI 主线程只渲染/调度。耗时 ASR、Demucs、翻译必须在 `multiprocessing.Process` 的 worker 中执行；不要用线程替换 worker 来解决 CUDA 回收问题。压制 FFmpeg 是 UI 发起的后台线程，但控件更新必须通过 `after()` 回到 Tk 主线程。
4. 若修改 worker 消息、payload、缓存指纹或输出命名，联动更新 `engine/protocol.py`、`controllers/task_controller.py`、`tools/smoke_pipeline_worker.py`/相关 smoke 和本文件。
5. 先跑本地、短、无联网验证；不要默认触发真实 API、真实模型下载、长视频 Demucs 或长 UI acceptance。
6. 完成后执行 secrets 和 v3 边界检查：

   ```powershell
   .\.venv\Scripts\python.exe tools\check_no_secrets.py
   .\.venv\Scripts\python.exe tools\v3_guard.py verify
   ```

   如果 `v3_guard verify` 报父目录变化，立即停止并报告路径，不要自动还原。

## 推荐验证命令

### 最快健康检查

`health` 会执行缓存/环境/编译/legacy 扫描、多个核心策略检查，以及离线模型注册表和一批 smoke；它仍可能运行字体/ASS 预览、FFmpeg preflight 等本地 UI/渲染脚本。它不做真实联网下载、不加载 Whisper 模型、不跑真实管线，也不运行真实 API 翻译。

```powershell
.\.venv\Scripts\python.exe tools\verify_next_stack.py `
  --profile health `
  --json-report test_logs\health.json
```

### Quick（含假 API 和 UI import）

```powershell
.\.venv\Scripts\python.exe tools\verify_next_stack.py --profile quick
```

### 按代码改动选择 smoke

```powershell
# 协议、worker、任务状态、恢复报告
.\.venv\Scripts\python.exe tools\smoke_pipeline_worker.py
.\.venv\Scripts\python.exe tools\smoke_task_state.py
.\.venv\Scripts\python.exe tools\smoke_task_recovery.py

# 翻译/歌词/字幕策略（均为本地或假 API）
.\.venv\Scripts\python.exe tools\smoke_translator.py --mode bilingual
.\.venv\Scripts\python.exe tools\smoke_translator.py --mode chinese_only
.\.venv\Scripts\python.exe tools\smoke_lyrics_context.py
.\.venv\Scripts\python.exe tools\smoke_subtitle_density.py
.\.venv\Scripts\python.exe tools\smoke_lyric_gap_detection.py
.\.venv\Scripts\python.exe tools\smoke_lyric_gap_repair.py

# ASS/字体/画廊 UI
.\.venv\Scripts\python.exe tools\smoke_ass_preview_window.py
.\.venv\Scripts\python.exe tools\smoke_style_workbench_preview.py
.\.venv\Scripts\python.exe tools\smoke_font_catalog.py
```

### 模型与真实管线（显式、耗时、按需）

```powershell
# 只检查本地模型目录，不下载
.\.venv\Scripts\python.exe tools\verify_model_download.py --model small

# 真实 worker 管线；要求 models_faster 中已有完整模型和 FFmpeg
.\.venv\Scripts\python.exe tools\smoke_full_pipeline.py `
  --model small `
  --media ".\smoke_media\yama_endroll_45s.wav" `
  --ass `
  --timeout 600
```

`verify_next_stack.py` 的真实 profile 参数：`network`（模型源 dry-run，会联网但不下载）、`model`（本地加载）、`pipeline`（真实 worker）、`full`（组合）、`download`（只有带 `--confirm-download` 才允许下载）。例如：

```powershell
# 仅示例；需要联网且不下载模型
.\.venv\Scripts\python.exe tools\verify_next_stack.py --profile network --model small

# 仅示例；危险操作，必须显式确认后才会下载
# .\.venv\Scripts\python.exe tools\verify_next_stack.py --profile download --model small --confirm-download
```

本文复核期间不执行下载、真实 API 或长任务。

## 改动边界与不变量

- Windows 子进程继续使用 `utils.ffmpeg_tools.subprocess_kwargs()` 的隐藏窗口/低优先级策略；不要改回 `os.system` 或漏掉 `creationflags=0x08000000`。
- `WorkerStdoutRedirector` 必须保留 `encoding`、`fileno()`、`isatty()`、`closed` 等属性，底层 C/C++ 库可能依赖它们。
- worker 取消先设置 `multiprocessing.Event`，超时再由 controller 终止进程树，以确保 CUDA/模型资源回收。
- ASR 与翻译缓存是 hash 契约；媒体指纹、模型 snapshot、Demucs/硬件模式、歌词、API URL/模型和布局策略变化都可能使缓存失效。修改时运行 `verify_next_stack --profile health` 和对应 cache smoke。
- `engine.pipeline_worker` 必须延迟导入 `core.generator`、`torch`、`faster_whisper`、`demucs`；launcher 启动不可加载重型 AI 包。
- 不把 `user_settings.json`、`user_presets.json`、API key、模型、`.engine`、`.venv`、`.cache`、`output`、媒体、日志提交到 Git。

## 打包与交付

Launcher 必须轻量，`.engine`、模型、缓存、字体和输出外置。修改 `app.py`/UI 导入关系后可执行：

```powershell
.\.venv\Scripts\python.exe tools\check_launcher_boundary.py
powershell -ExecutionPolicy Bypass -File .\tools\package_launcher.ps1 -Clean
.\.venv\Scripts\python.exe tools\check_packaging_size.py

# 本地 packaged smoke，可按需把外部运行时链接进 dist（不会复制大文件）
powershell -ExecutionPolicy Bypass -File .\tools\link_dist_runtime.ps1 -LinkCache
```

GPU wheelhouse 脚本参数已经核实：`prepare_engine_gpu_wheels.ps1` 和 `install_engine_gpu_wheels.ps1` 支持 `-Proxy`/`-Wheelhouse`（prepare）及 `-Wheelhouse`，两者均支持 `-Scope CTranslate2|TorchCuda|All`。这类安装会修改 `.engine`，不要在普通文档/快测任务中自动执行。

## 提交前检查清单

- 只改了 `UtaSyncNext` 仓库；`git -C .. status` 不应出现由本次任务造成的 v3 变化。
- 代码和文档中的路径、脚本参数与当前文件一致。
- 针对改动跑了最小相关 smoke，再跑 `verify_next_stack --profile health`（若改动影响范围较大则跑 `quick`）。
- `check_no_secrets.py` 和 `v3_guard.py verify` 通过。
- 未执行真实下载/API/长视频任务，除非任务明确要求并具备对应输入。
