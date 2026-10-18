# UtaSyncNext 配置与运行环境

复核日期：2026-10-13

UtaSyncNext 的 “Next” 是产品的下一代桌面管线名称，不是 Next.js 应用。代码是 Windows 优先的 Python/CustomTkinter 桌面程序，没有 npm、网页路由、HTTP 服务端或登录鉴权层。本文的路径均相对于 `UtaSyncNext/`。

## 第一次安装和启动

UI 环境和重型识别环境分开安装。UI 启动器使用 `.venv`；Faster-Whisper、Demucs、Torch 等重型依赖使用 `.engine`。

```powershell
# 创建 UI 环境并安装轻量依赖
powershell -ExecutionPolicy Bypass -File tools/bootstrap_venv.ps1

# 同时安装重型引擎（会下载 CUDA/Torch 依赖，可能需要数 GB）
powershell -ExecutionPolicy Bypass -File tools/bootstrap_venv.ps1 -Engine

# 已有 UI 环境时，只安装引擎
powershell -ExecutionPolicy Bypass -File tools/bootstrap_venv.ps1 -EngineOnly -Engine

# 启动
powershell -ExecutionPolicy Bypass -File run_next.ps1
# 或双击/命令行
run_next.cmd
```

`run_next.ps1` 和 `run_next.cmd` 要求 `UtaSyncNext/.venv/Scripts/python.exe` 存在，并把工作目录切换到 `UtaSyncNext/`。它们会把 Hugging Face 和 XDG 缓存放在项目内的 `.cache/`，并设置 `HF_HUB_DISABLE_XET=1`。任务运行前还必须能从 `PATH` 找到 `ffmpeg` 和 `ffprobe`。

安装脚本的可选参数：`-Engine` 安装 `.engine`，`-EngineOnly` 跳过 UI 环境，`-Proxy` 给 pip 传代理（可写端口号、`host:port` 或带协议的 URL），`-Python` 指定创建虚拟环境的 Python 命令。

## 环境变量

下面是代码实际读取或设置的变量。除明确写有“内部”的变量外，通常不需要在启动前手动设置。

| 变量 | 作用 | 默认/注意 |
| --- | --- | --- |
| `UTASYNCNEXT_APP_ROOT` | 覆盖应用根目录；配置、模型、缓存和输出都跟随该目录 | 未设置时，源码运行取包根目录，冻结程序取 exe 所在目录 |
| `UTASYNCNEXT_ENGINE_ROOT` | 指定外部重型引擎根目录 | 未设置时依次检查 `.engine`、`.venv` |
| `UTASYNC_UI_VARIANT` | 覆盖 UI 布局选择 | 值为 `classic` 或 `next`；未设置或无效值都回到 `next`。设置文件中的 `ui_variant` 会保存切换偏好，但当前启动解析以该环境变量为准 |
| `UTASYNCNEXT_FORCE_CPU_ASR` | 强制 Faster-Whisper 使用 CPU `int8` | 设为 `1`；适合 CUDA DLL/驱动故障排查 |
| `UTASYNCNEXT_DEMUCS_RUNNER` | 覆盖 `engine/demucs_runner.py` 路径 | 仅在需要自定义 Demucs 启动器时使用 |
| `HF_HOME`、`HUGGINGFACE_HUB_CACHE`、`HF_HUB_CACHE`、`TRANSFORMERS_CACHE`、`XDG_CACHE_HOME` | Hugging Face/通用缓存位置 | 启动器和 worker 会覆盖为项目内 `.cache` 路径 |
| `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`（及小写形式） | 下载或 HTTP 请求的临时代理 | UI 的“代理”设置只在相关操作期间注入；不会从 `.env` 自动读取 API Key |
| `TORCH_HOME` | Demucs 权重缓存根目录 | 若设置，权重检查位置为 `$TORCH_HOME/hub/checkpoints`；否则为 `.cache/torch/hub/checkpoints` |

worker 运行时还会设置 `UTASYNC_SITE_PACKAGES`、`UTASYNC_HOOKED_ENV`，它们是内部路径，不应作为持久配置。

## `user_settings.json` 设置

设置文件是 `app_root()/user_settings.json`，启动时与 `DEFAULT_SETTINGS` 合并并归一化；文件不存在或 JSON 损坏时回退到默认值。UI 保存媒体路径、歌词草稿和所有面板设置。该文件可能包含 API Key，不能提交、上传或放进公开压缩包；仓库提供的 `user_settings.example.json` 只用于参考。

### 代码默认值

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `ui_variant` | `next` | 启动布局；也可被 `UTASYNC_UI_VARIANT` 覆盖 |
| `media_path` | 空 | 最近一次媒体路径 |
| `cached_lyrics` | 空 | 工作区歌词草稿 |
| `audio_type` | `Live 现场演唱 (常规/摇滚)` | 另有窄画幅高速 Live、柔和 Live、访谈/播客 |
| `hardware_mode` | `自动匹配显存 (推荐)` | Demucs 启用时生效；自动档按 GPU 显存选择保守/均衡/快速 |
| `use_demucs` | `先跳过 Demucs (极速/推荐先出轴)` | 访谈模式始终跳过；开启 Demucs 前需准备权重 |
| `model_size` | `large-v2 (战神级/推荐)` | 也可选 `large-v3-turbo (极速/演唱慎用)`、`large-v3`、`medium`、`small`、`base`；括号只是提示，实际取值只用空格前的模型名 |
| `output_mode` | `中日双语字幕` | 或 `纯中文字幕` |
| `ass_preset` | `默认双语 (纯白+浅灰)` | 选择 `不生成 (仅SRT)` 可跳过 ASS |
| `ch_font_override` / `jp_font_override` | `跟随预设` | 中文/日文字体覆盖 |
| `ch_size_override` / `jp_size_override` | `跟随预设` | 字号覆盖 |
| `aspect_ratio` | `16:9 (常规宽屏/默认)` | 也支持自动侦测、1:1、4:3、9:16 |
| `fade_mode` | `智能动态呼吸 (默认/推荐)` | 另有硬切和强制淡入淡出 |
| `hires_export` | `false` | 开启后输出 MKV + FLAC 无损音频，关闭时输出 MP4 + AAC；视频画质两种模式相同，取舍见 `OUTPUT_FILES.md` 的“硬字幕成品”一节 |
| `api_preset` / `api_provider` | `SiliconFlow (DeepSeek-V3)` / `SiliconFlow` | 仅作 UI 预设/来源记录 |
| `api_url` | `https://api.siliconflow.cn/v1` | OpenAI-compatible Base URL |
| `api_model` | `deepseek-ai/DeepSeek-V3` | 由用户选择的模型名会原样传给 API |
| `api_key` | 空 | 非空才会调用翻译 API；保存为本地明文设置 |
| `proxy_url` / `model_download_proxy` | 空 | 归一化后始终保持相同值 |
| `api_preset_design` | `与翻译引擎一致 (默认)` | 视觉/设计设置预留 |
| `api_url_design` / `api_model_design` / `api_key_design` | 空 | 设计 API 设置；当前翻译管线使用上面的 `api_*` |

UI 实际可选项和预设定义在 [`ui/sidebar_options.py`](../ui/sidebar_options.py)。翻译预设是 SiliconFlow、Kimi（月之暗面）、Google Gemini 和完全自定义；这些预设只填 Base URL/模型，仍需用户在面板填写密钥。代码不会从环境变量或 `.env` 补填 `api_key`。

兼容旧设置时会归一化英文值（例如 `Bilingual`、`Chinese Only`、`Skip`），并把 `demucs_mode`、`ch_font`、`jp_font`、`ch_size`、`jp_size` 以及旧视觉 API 键映射到当前键。代理只接受空值、端口号或 `http://`、`https://`、`socks5://`、`socks5h://`。

## 模型、权重和字体目录

* Faster-Whisper 模型位于 `models_faster/`，由 Hugging Face `snapshot_download` 管理。模型管理器支持官方源、`https://hf-mirror.com` 或自定义 endpoint；任务本身只使用已完整校验的本地快照，不会因缺模型而在任务中偷偷联网。
* 模型仓库映射和完整性检查见 [`engine/model_downloader.py`](../engine/model_downloader.py) 与 [`engine/model_registry.py`](../engine/model_registry.py)。
* Demucs `htdemucs_ft` 权重位于 `.cache/torch/hub/checkpoints/`（或 `TORCH_HOME` 指定目录）。开启 Demucs 且权重缺失时，任务会在分离前停止；可先下载权重，或在 UI 选择跳过 Demucs。

### 首次启动安装引擎

打包版可以在“引擎与模型管理”窗口的“检查与安装”区域点击“安装/修复引擎”。窗口底部把常用操作、检查与安装分开，旧缓存位置和 GPU 升级指南默认收在“更多工具”中，避免窄窗口裁剪按钮。它读取一个带 SHA256 的 JSON 清单，分卷下载并校验 `.engine` 压缩包，再原子替换本地引擎目录；下载过程不会在普通任务中隐式触发。默认清单地址是：

`https://github.com/zzzzzezzz/UtaSyncNext/releases/latest/download/utasyncnext-engine-manifest.json`

打开窗口时会异步检查清单。正式发布包会在最新 Release 中同时提供清单和引擎分卷；如果仍返回 404，通常表示下载到了旧版本、Release 仍是草稿，或发布资产名称不匹配。界面会显示“引擎资源未发布”并停用安装按钮，不会改动已有 `.engine`；修正发布资产后点击“重新检查”即可恢复。网络或代理暂时不可用时同样可以重新检查。若本机已经有可用引擎，资源检查失败不会影响现有任务。

如需切换到镜像或自建对象存储，可在启动前设置 `UTASYNCNEXT_ENGINE_MANIFEST_URL`。清单中的每个资产都必须是 ZIP，并包含同一个 `.engine` 的相对路径；正式分卷还包含 `python-runtime/` 便携 Python、`python._pth` 和修正后的 `pyvenv.cfg`，因此 Demucs 不依赖发布者电脑上的 Python 路径。安装器会逐个校验后解压到同一个 `.engine`。安装完成后重启软件，再从模型管理器选择要使用的 Faster-Whisper 模型下载。
* 用户导入的 `.ttf`、`.otf`、`.ttc`、`.otc` 字体位于 `fonts/`，由压制时的 ASS/FFmpeg 使用；不扫描或安装整个 Windows 字体注册表。

## 不应提交的内容

不要提交 `.venv/`、`.engine/`、`models_faster/`、`.cache/`、`output/`、`fonts/`、媒体文件、日志、`user_settings.json` 和 API Key。打包边界及检查命令见 [`packaging/README.md`](../packaging/README.md)。

