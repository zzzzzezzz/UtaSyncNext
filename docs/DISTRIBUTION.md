# 面向使用者的发布包

UtaSyncNext 的发布包分成两层：GitHub 上的轻量启动器和首次运行时下载的外置引擎。启动器不会把 Torch、Demucs、Faster-Whisper 或 Whisper 模型塞进 EXE，因此下载速度和磁盘占用都更容易控制。引擎压缩包放在 Cloudflare R2 等对象存储，GitHub 只保存基础包和小型清单。

## v0.1.3 的文件

GitHub Release 应包含这些文件：

- `UtaSyncNext-basic-v0.1.3.zip`：解压后即可启动的基础包。
- `utasyncnext-engine-manifest.json`：安装器读取的 SHA256 清单。

清单地址固定为：

`https://github.com/zzzzzezzz/UtaSyncNext/releases/latest/download/utasyncnext-engine-manifest.json`

清单中的 `assets[].url` 指向 R2 的公开 HTTPS 地址。发布新版本时，先把引擎分卷上传到 R2，再把带有 R2 地址和 SHA256 的清单上传到同一个最新 Release。只上传基础 ZIP 而不上传清单，会再次出现 HTTP 404。

R2 桶默认是私有的。长期发布应绑定自定义域名；`r2.dev` 只适合测试，存在访问限制。当前引擎总量约 4.2 GB，低于 R2 Standard 每月 10 GB-month 的免费存储额度，但免费额度不是无限量，仍应在 Cloudflare 控制台监控存储和操作次数。

v0.1.3 的 3 个分卷直接放在桶根目录，清单里也使用根路径 URL；重新生成清单时不要再加 `utasyncnext/v0.1.3` 前缀，否则下载会返回 404。新版本建议给 `tools\upload_r2_engine_assets.py --prefix utasyncnext/vX.Y.Z` 和 `tools\build_engine_manifest.py --base-url ...` 使用同一个前缀。

## 新电脑的使用顺序

1. 下载并解压基础包，启动 `UtaSyncNext.exe`。
2. 打开“引擎与模型管理”。如果使用代理，在“代理”中填端口号（例如 `10808`）或完整地址。
3. 等待“引擎资源可用”，点击“安装/修复引擎”。安装器会显示总量、逐卷进度，并在校验失败时保留可继续的临时下载。
4. 安装完成后重启软件，再选择需要的 Faster-Whisper 模型并下载。模型按需下载，不随 Release 强制安装。
5. 任务还需要 `ffmpeg` 和 `ffprobe`。如果系统没有它们，任务预检会明确提示；当前发布包不替用户静默安装 GPL 构建的 FFmpeg。

引擎分卷包含便携 Python 3.10 运行时，Demucs 不再依赖维护者电脑上的 `E:\Python310` 路径。安装器只写入 Next 自己的 `.engine` 目录，不会改动 V3 环境。

## 维护者发布检查

在发布前从仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe tools\build_engine_assets.py .engine build\engine_release_v0.1.3 `
  --portable-python-root E:\Python310 --max-bytes 1600000000 --clean
.\.venv\Scripts\python.exe tools\build_engine_manifest.py `
  build\engine_release_v0.1.3 build\engine_release_v0.1.3\utasyncnext-engine-manifest.json `
  --version 0.1.3-cuda121-portable `
  --base-url https://download.example.com/utasyncnext/v0.1.3
.\.venv\Scripts\python.exe tools\smoke_engine_assets.py `
  build\engine_release_v0.1.3 --output build\engine_asset_verify_v0.1.3
```

最后一个 smoke 会把所有分卷解压到临时应用目录，并从便携运行时导入 `torch`、`torchaudio`、`faster_whisper` 和 `demucs`。每个 ZIP 都必须小于 2 GiB；不要把 `.engine`、模型、缓存或用户设置提交到 Git。

上传 R2 后，再用实际公开域名重新生成清单。例如：

```powershell
.\.venv\Scripts\python.exe tools\build_engine_manifest.py `
  build\engine_release_v0.1.3 build\engine_release_v0.1.3\utasyncnext-engine-manifest.json `
  --version 0.1.3-cuda121-portable `
  --base-url https://download.example.com/utasyncnext/v0.1.3
```

发布前逐个用浏览器或 `Invoke-WebRequest` 检查 R2 URL 能返回 200/206，并核对清单中的 `size` 和 `sha256`。清单可以继续放 GitHub；安装器会从清单里的 R2 URL 下载，不要求 ZIP 也在 GitHub。
