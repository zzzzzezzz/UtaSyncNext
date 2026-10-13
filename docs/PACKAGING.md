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
