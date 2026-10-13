# UtaSyncNext 故障排查

复核日期：2026-10-13

先确认这是 Windows/Python 桌面程序：不要用 `npm run dev`、Next.js 路由或浏览器环境来排查。下面命令都在 `UtaSyncNext/` 目录执行，优先使用 `.venv/Scripts/python.exe`。

## 快速检查

```powershell
cd E:\traecode\UtaSync\UtaSyncNext

# 只检查引擎目录、模块版本和 CUDA DLL；不加载模型
.venv\Scripts\python.exe tools\check_engine_runtime.py

# 深度检查会导入 torch 并查询 CUDA
.venv\Scripts\python.exe tools\check_engine_runtime.py --deep

# 分层验证：health/quick 不下载模型；network/model/full 可能访问模型源
.venv\Scripts\python.exe tools\verify_next_stack.py --profile health
.venv\Scripts\python.exe tools\verify_next_stack.py --profile quick

# 仅检查模型源清单（是否联网由参数决定）
.venv\Scripts\python.exe tools\check_model_sources.py --help
```

不要为了排查而直接运行真实模型、API 或下载命令；先用 `health`/`quick` 和日志定位问题。需要网络模型源检查时，再明确选择 `network` 或 `model` profile，并准备好代理和磁盘空间。

## 启动失败

### “`.venv` 未找到”

启动脚本只认 `UtaSyncNext/.venv/Scripts/python.exe`。运行：

```powershell
powershell -ExecutionPolicy Bypass -File tools\bootstrap_venv.ps1
```

若 Python 命令不是 `python`，例如使用 `py -3.11`：

```powershell
powershell -ExecutionPolicy Bypass -File tools\bootstrap_venv.ps1 -Python py
```

`-Python` 只影响创建环境的命令；启动仍然使用已经创建的 `.venv`。

### “需要安装 Next 引擎”或“缺少 faster_whisper/demucs/torch/torchaudio”

UI 依赖和重型引擎分开。安装引擎：

```powershell
powershell -ExecutionPolicy Bypass -File tools\bootstrap_venv.ps1 -Engine
.venv\Scripts\python.exe tools\check_engine_runtime.py
```

如果重型环境不在 `.engine`，设置 `UTASYNCNEXT_ENGINE_ROOT` 指向一个包含 `Scripts` 和 `Lib/site-packages` 的虚拟环境。不要把 Torch/Demucs 直接装进父目录 v3 环境来“碰运气”；Next 的定位器只检查 Next 自己的环境。

### 窗口能打开但任务立刻停止

任务开始前会检查媒体文件、Next 引擎、`ffmpeg` 和 `ffprobe`。确认：

```powershell
where.exe ffmpeg
where.exe ffprobe
ffmpeg -version
ffprobe -version
```

如果 `where.exe` 没有结果，请安装 FFmpeg 并把其 `bin` 目录加入启动 UtaSyncNext 的进程 `PATH`。仅在另一个终端改 PATH 不会改变已经运行的 GUI 进程。

### 双击后没有任何反应

启动器每次运行都会在 exe 同目录追加一行 `launcher_startup.log`：

```text
2026-10-13 17:52:03 pid=1234 started
2026-10-13 17:53:11 pid=5678 second launch raised=True
```

- 文件里没有对应时间的那一行：exe 根本没跑到 Python，先确认双击的是
  `dist\UtaSyncNext\UtaSyncNext.exe`，而不是桌面上的 V3 快捷方式（V3 快捷方式指向
  `E:\UtaSync_V3.0_满血免安装版`）。
- `started`：这次是正常启动。
- `second launch raised=True`：已经有一个实例在跑，启动器把它的窗口恢复到前台；
  如果系统拒绝置前，它会短暂置顶并闪烁任务栏按钮。第二个进程随即自行退出。
- `second launch raised=False`：锁被一个没有窗口的进程占用（通常是上次崩溃或被强杀
  留下的僵尸进程），在任务管理器结束 `UtaSyncNext.exe` 后重新启动。

启动阶段崩溃会另外写 `launcher_crash.log`，位置同样是 exe 同目录。

## 模型、CUDA 和 Demucs

### “本地未找到 Faster-Whisper 模型”

任务阶段只接受 `models_faster/` 下通过完整性检查的本地快照。检查模型状态：

```powershell
.venv\Scripts\python.exe tools\verify_model_download.py --help
.venv\Scripts\python.exe tools\verify_next_stack.py --profile health
```

使用 UI 的引擎/模型管理器，或在确认网络、源和磁盘空间后使用模型下载工具。模型源失败时可在 UI 设置官方源、`hf-mirror.com`、自定义 endpoint 和代理；缺模型不会在任务中隐式联网。

### CUDA 失败、显存不足或 DLL 缺失

ASR 会先尝试 CUDA，加载或转写阶段遇到 CUDA 运行时问题时回退 CPU `int8`。为了验证管线而强制 CPU：

```powershell
$env:UTASYNCNEXT_FORCE_CPU_ASR = "1"
run_next.ps1
```

当前进程已启动后再设置变量不会影响它。`tools/check_engine_runtime.py --deep` 会报告 Torch CUDA 状态；如果 CUDA wheel 与驱动不匹配，优先让它使用 CPU 完成排查，再处理驱动或 `.engine` 依赖。

### Demucs 权重缺失

启用 Demucs 时，worker 会在分离前检查 `htdemucs_ft` 权重。默认目录是：

```text
UtaSyncNext/.cache/torch/hub/checkpoints/
```

如果设置了 `TORCH_HOME`，则是 `$TORCH_HOME/hub/checkpoints/`。缺权重时可先导入/下载权重；若目标是先生成打轴，请在 UI 选择“先跳过 Demucs”。访谈/播客模式本身会跳过 Demucs。

## 翻译 API、代理和密钥

翻译只在 UI 的 `api_key` 非空时运行。代码通过 OpenAI-compatible `api_url` 和 `api_model` 请求，超时 180 秒，SDK 自动重试关闭。常见检查项：

1. Base URL 是否是服务商要求的 OpenAI-compatible 根地址（通常包含 `/v1`）；不要把完整 `/chat/completions` 路径填入。
2. 模型名是否是服务商实际接受的字符串；代码不会替换用户选择的模型。
3. Key 是否仍有效、账户是否有额度；HTTP 400/401/403/404/422 会被视为不可重试类错误。
4. 代理是否是端口、`host:port` 或 `http(s)/socks5` URL。代理设置同时作用于翻译和模型下载，并在操作期间临时注入环境代理。

API Key 保存在 `user_settings.json`，不是环境变量。不要把该文件、错误压缩包或屏幕截图发给他人。错误日志和恢复报告会过滤设置中键名含 `key`、`token`、`secret`、`password` 的值，但 API trace 仍应按敏感运行产物对待。

## 字幕内容异常

### 原文 SRT 复用不符合预期

ASR 复用需要同时存在有效 `<任务名>_极致打轴.srt` 和匹配的 `<任务名>_打轴指纹.hash`。v7 指纹包含媒体边缘指纹、模型快照、音频/分离/计时策略；参考歌词不在指纹中，因此改歌词通常不会触发重跑 ASR。媒体内容、模型快照、分离策略或安全区规则变化会使缓存失配。

### 歌词缺轴预览没有出现

这是有条件的 review 预览，不是自动修复保证。必须先提供歌词并检测到候选，且本次不能命中 ASR 缓存；媒体要是 Live/柔和 Live，候选窗口不超过 120 秒、未被保守拒绝规则拦截，局部词级证据还要覆盖所有待补歌词并且不与现有轴重叠。访谈、缓存复用、证据不足和重叠都会保留正式 SRT，不生成或不采用预览。

查看 `<任务名>_极致打轴_歌词缺轴检查.json`、`*_歌词词级对齐_*.srt` 和 `*_歌词缺轴修复预览_*_证据.json`，结合日志人工试听。正式字幕不会因为该检查自动写入预览结果。

### 翻译结果不完整或重复请求

查看 `subtitles/<任务名>_极致打轴_翻译缓存.json` 与 `*_api_trace.json`。成功批次会保留；翻译完成标记只有在全批次成功且目标 SRT 有效时才写入。修改歌词、API URL、模型、源 SRT、输出模式或布局后，v8 完成标记会失配，但仍可复用相同批次的有效缓存。若缓存内容本身来自错误设置，可通过 UI 的当前媒体缓存清理清除音频/分离目录；不要手动删除仍需恢复的翻译缓存，除非你确认要从头请求。

## 错误日志和恢复

任务异常会在当前任务目录写入：

```text
output/<任务名>/logs/utasyncnext_error_YYYYMMDD_HHMMSS.txt
output/<任务名>/logs/utasyncnext_recovery_YYYYMMDD_HHMMSS.json
```

错误日志包含最后状态、错误、已保留工件和脱敏后的运行日志；恢复 JSON 记录是否有源 SRT、音频缓存、翻译批次缓存和指纹。它只描述可复用范围，不承诺任意半成品都能安全续接：

* 有源 SRT 和匹配指纹：下次可跳过 ASR，继续后处理/翻译。
* 只有源 SRT：缺指纹，系统不会直接复用。
* 只有音频缓存：可减少提取/分离，但仍需重新听写。
* 有翻译批次缓存但没有可复用 ASR：需先生成并校验源 SRT，不能保证直接续接。

性能报告位于 `output/<任务名>/logs/utasyncnext_performance_*.txt` 和 `.json`。硬字幕压制失败时，UI 会写 `utasyncnext_burn_error_*.txt`；FFmpeg 会先尝试 NVIDIA `h264_nvenc`，再回退 CPU `libx264`。

## 打包和安全检查

轻量 launcher 不包含 Torch、Demucs、Faster-Whisper、CUDA DLL 或模型。打包前后使用：

```powershell
.venv\Scripts\python.exe tools\check_launcher_boundary.py
powershell -ExecutionPolicy Bypass -File tools\package_launcher.ps1 -Clean
.venv\Scripts\python.exe tools\check_packaging_size.py
.venv\Scripts\python.exe tools\smoke_icon_assets.py
.venv\Scripts\python.exe tools\check_no_secrets.py
```

Next 仓库的开发安全边界还要求：

```powershell
.venv\Scripts\python.exe tools\v3_guard.py begin
# 修改/测试完成后
.venv\Scripts\python.exe tools\v3_guard.py verify
```

父目录 `UtaSync` 是 v3 只读参考；不要在父目录运行格式化、安装、清理或提交命令。更多打包约束见 [`packaging/README.md`](../packaging/README.md)，运行入口见 [`run_next.ps1`](../run_next.ps1)。

