# Launcher 打包

UtaSyncNext 的 Launcher 是轻量 PyInstaller 包；`.engine`、`models_faster`、`.cache`、`fonts` 和 `output` 保持在包外。完整规则和资源说明见 [`packaging/README.md`](../packaging/README.md)。打包版可通过“引擎与模型管理 → 安装/修复引擎”下载带校验的外置引擎，模型仍按用户选择单独下载。

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
4. `user_settings.json`、API Key、媒体、日志、`output`、`.cache` 不进入发布包。注意 `dist\UtaSyncNext` 是本机运行目录，里面保留着你自己的 `user_settings.json`（含明文 API Key）、`output`、`.cache` 和 `performance`；要发给别人时只打包 `UtaSyncNext.exe` 和 `_internal`，或者先把这些运行文件删掉再压缩。
5. 运行 `check_no_secrets.py` 和 `v3_guard.py verify`。
6. 直接从 `dist\UtaSyncNext` 启动一次成品，确认窗口出现、并且收到关闭消息后进程自行退出：

   ```powershell
   .\.venv\Scripts\python.exe tools\probe_launcher_close.py
   ```

   这个探针只对标题为 `UtaSyncNext` 的窗口发 `WM_CLOSE`。不要用读 `Process.MainWindowHandle` 的 PowerShell 脚本代替：Windows 11 上进程第一个可见顶层窗口常常是 `UAC_InputIndicatorOverlayWnd` 浮层，`CloseMainWindow()` 会打到那个窗口，于是会报出一个应用本身没有的“关不掉”结论。

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
6. 分发打包产物时用 `tools\package_launcher.ps1 -Clean`。若发布“到手即用”版本，额外上传经过瘦身的 `.engine` 分卷和 `utasync-engine-manifest.json`；模型仍作为首次启动后的按需下载项，不随包分发。

## 引擎清单和首次安装

引擎清单由 `engine/runtime_installer.py` 读取。最小格式如下：

```json
{
  "version": "2026.10.17-cu121",
  "assets": [
    {
      "name": "utasyncnext-engine-part1.zip",
      "url": "https://example.invalid/utasyncnext-engine-part1.zip",
      "size": 1234567890,
      "sha256": "64 位十六进制 SHA256"
    }
  ]
}
```

每个 ZIP 都必须解压出同一 `.engine` 目录的相对结构。安装器支持断点续传、逐文件 SHA256 校验、安全解压和失败回滚；发布前应先用临时清单和 `tools/smoke_runtime_installer.py` 验证。清单地址可由 `UTASYNCNEXT_ENGINE_MANIFEST_URL` 覆盖，便于使用 CDN 或私有对象存储。

