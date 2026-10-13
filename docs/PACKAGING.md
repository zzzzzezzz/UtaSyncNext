# Launcher 打包

UtaSyncNext 的 Launcher 是轻量 PyInstaller 包；`.engine`、`models_faster`、`.cache`、`fonts` 和 `output` 保持在包外。完整规则和资源说明见 [`packaging/README.md`](../packaging/README.md)。

## 构建前

```powershell
Set-Location E:\traecode\UtaSync\UtaSyncNext
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tools\check_launcher_boundary.py
```

边界检查会扫描 Launcher 源码和 spec：启动链路不得直接 import `torch`、`faster_whisper`、`demucs`、`whisper` 或 `ctranslate2`，也不得收集引擎、模型、用户输出和缓存。

## 构建、审计和本地运行

```powershell
powershell -ExecutionPolicy Bypass -File tools\package_launcher.ps1 -Clean
.\.venv\Scripts\python.exe tools\check_packaging_size.py
.\.venv\Scripts\python.exe tools\smoke_icon_assets.py
powershell -ExecutionPolicy Bypass -File tools\link_dist_runtime.ps1
```

构建脚本从 `build\launcher_source` 隔离复制源代码，避免父工作区的旧 V3 包被 PyInstaller 自动收集。`link_dist_runtime.ps1` 仅为本机 smoke 在 `dist\UtaSyncNext` 下创建指向 Next 自己 `.engine` 和 `models_faster` 的 junction，不复制大文件，也不读取或链接 V3。

## 发布前检查

1. `check_launcher_boundary.py` 和 `check_packaging_size.py` 通过。
2. Launcher 冷启动不加载重 AI 库，外部 FFmpeg 仍由用户安装并加入 PATH。
3. `.engine` 已按 `requirements-engine.txt` 准备，模型 snapshot 已通过完整性检查。
4. `user_settings.json`、API Key、媒体、日志、`output`、`.cache` 不进入发布包。
5. 运行 `check_no_secrets.py` 和 `v3_guard.py verify`。

Launcher 本身不是完整离线安装包；没有外置引擎或模型时，UI 可以打开，但任务预检会明确提示缺少依赖。

## 入库和发布前的检查

1. 提交前跑 `tools\check_no_secrets.py` 和 `tools\v3_guard.py verify`；`.githooks/pre-commit`
   已经强制这两项，克隆后先执行 `git config core.hooksPath .githooks`。
2. 确认没有把 `.venv`、`.engine`、`models_faster`、`output`、`performance`、`.cache`、`fonts`、
   `test_logs`、媒体文件、日志、`user_settings.json`、`user_presets.json` 提交进去。
3. 跑一遍 `tools\verify_next_stack.py --profile quick`，它比 `health` 多出真实 UI import 检查和假 API 翻译 smoke。
4. 推送后确认 `.github/workflows/verify.yml` 是绿的。CI 里 `network-proxy` 和 `preset-catalog` 是 SKIP，
   因为它们分别依赖本机引擎和未入库的 `fonts/`，这是预期结果，不是回归。
5. 决定是否随仓库分发 `fonts/` 里的字体。思源黑体 CN/JP、霞鹜文楷、M PLUS 1p、Zen Kaku Gothic New
   都是 SIL OFL 许可，随仓库分发需要一并提供各自的许可文本；不分发则接受新克隆回退到 Windows 自带字体。
6. 分发打包产物时用 `tools\package_launcher.ps1 -Clean`，并把 `.engine` 和 `models_faster` 作为外置依赖单独说明。

