# UtaSyncNext 模块地图（2026-10-13 复核）

## 先确认项目形态

`UtaSyncNext` 是 Python 3.10 + CustomTkinter 的 Windows 桌面应用，**不是 Next.js、React 或 Node.js 项目**。仓库没有 `package.json`、`src/app`、页面路由或 TypeScript 构建链；这里的 “Next” 是产品代号。Trae/Cursor 二次开发时，应把 `UtaSyncNext` 作为 Python 工作区打开，不要按 Web 项目生成 `npm` 脚本或 Next.js 路由。

## 运行时分层

```text
app.py
  └─ ui.main_window.MainWindow（轻量 GUI）
       └─ controllers.task_controller.TaskController
            └─ multiprocessing.Process
                 └─ engine.pipeline_worker.run_pipeline_worker
                      ├─ site.addsitedir(.engine/Lib/site-packages)
                      ├─ core.generator（Faster-Whisper / 可选 Demucs）
                      ├─ core.translator（OpenAI-compatible API）
                      └─ core.ass_maker（ASS）
```

主 worker 是当前 `.venv` Python 创建的 `multiprocessing.Process`；`engine.pipeline_worker._prepare_engine_runtime()` 会把 `.engine/Lib/site-packages` 加入当前进程的 `sys.path`，并注册 DLL/`PATH`。因此它不是通过 `EngineLocation.python` 另起一个 Python 解释器来跑整条管线。Demucs 命令是例外：`core/generator.py` 优先使用 `UTASYNC_HOOKED_ENV\Scripts\python.exe engine\demucs_runner.py`，找不到时才调用 `demucs` 命令。不要把这两个边界混写。

## 启动与控制层

| 路径 | 职责与二次开发入口 |
| --- | --- |
| [`app.py`](../app.py) | `freeze_support()`、缓存环境、工作目录、单实例锁、主窗口启动；启动阶段不导入重型 AI 库。 |
| [`controllers/task_controller.py`](../controllers/task_controller.py) | 收集 payload、依赖预检、创建/取消 worker、轮询队列、把 `done/error` 和日志交给 UI。一个任务只有一个 worker。 |
| [`engine/protocol.py`](../engine/protocol.py) | GUI/worker 消息字典：`log`、`progress`、`status`、`done`、`error`。修改字段时要同步 controller、烟测和文档。 |
| [`engine/worker_entry.py`](../engine/worker_entry.py) | 轻量 worker demo，仅用于检查环境/FFmpeg；真实管线入口是 `pipeline_worker.py`。 |
| [`ui/main_window.py`](../ui/main_window.py) | 组装窗口、媒体导入、设置保存、任务状态、ASS 压制入口；通过 `after()` 把后台结果送回 Tk 主线程。 |
| [`ui/task_state.py`](../ui/task_state.py) | 无 Tk 依赖的不可变 `TaskSnapshot` 和 `TaskPhase`：EMPTY/READY/PREFLIGHT/QUEUED/RUNNING/CANCELLING/SUCCEEDED/FAILED/CANCELLED。 |

## UI 层

- `ui/experimental_sidebar.py`、`ui/sidebar.py`：Next/经典侧栏；`MainWindow` 默认使用 Next 工作台。可用环境变量 `UTASYNC_UI_VARIANT=classic` 临时切换经典布局。
- `ui/basic_settings_panel.py`：音频模式、硬件分离策略、Demucs、Whisper 模型、双语/纯中文。
- `ui/api_settings_panel.py`：OpenAI-compatible URL、模型、API key、代理和设计 API 设置。
- `ui/visual_settings_panel.py`、`ui/gallery.py`：ASS 预设、字体、画幅、字号、淡入淡出、花字预览。
- `ui/ass_actions.py`、`ui/ass_preview_frame.py`、`ui/ass_preview_window.py`：ASS 预览/重绘；`ui/output_actions.py` 管理输出路径、打开目录和复制路径。
- `ui/engine_manager.py`、`ui/engine_manager_support.py`：模型源、代理、下载预检、本地模型完整性和引擎状态。
- `ui/font_catalog.py`、`core/font_names.py`：只管理应用 `fonts/` 中的用户字体与字体搭配，不扫描/安装整个系统字体注册表。
- `ui/log_formatter.py`、`ui/workspace.py`：日志、状态和结果展示；不要在 worker 或后台线程直接操作控件。

## 核心处理层

| 路径 | 作用 |
| --- | --- |
| [`engine/pipeline_worker.py`](../engine/pipeline_worker.py) | 实际管线编排：媒体/模型预检 → ASR 缓存 → 音频与可选 Demucs → Faster-Whisper → 诊断/歌词缺轴预览 → 可选翻译 → ASS → 性能报告与 artifacts。 |
| [`core/generator.py`](../core/generator.py) | 音频提取、Demucs 分段/缓存、Faster-Whisper 加载与 SRT；优先 CUDA，运行时失败回退 CPU int8。 |
| [`core/translator.py`](../core/translator.py) | OpenAI-compatible 翻译、批次缓存、断点续传、双语覆盖/说话人标签/歌词顺序校验；真实 API 仅显式 smoke。 |
| [`core/ass_maker.py`](../core/ass_maker.py) | SRT 解析、ASS 头和 Dialogue、双语样式、字体映射、淡入淡出；`ASS_PRESETS` 是预设入口。 |
| [`core/ass_config.py`](../core/ass_config.py) | 从设置和预设构造 ASS 配置。 |
| [`core/subtitle_policy.py`](../core/subtitle_policy.py) | 画幅安全区、字号/行宽、字幕密度、长 cue 拆分和布局日志。 |
| `core/asr_diagnostics.py`、`opening_lyrics_cleanup.py`、`watermark_hallucination_cleanup.py` | ASR 证据、开头/结尾歌词锚定清理、明显水印式幻觉清理。 |
| `core/lyrics_context.py`、`lyric_gap_detection.py`、`lyric_gap_repair.py`、`lyric_assisted_alignment.py` | 歌词段落选择、缺轴候选、严格对齐和“仅预览不自动写正式字幕”的修复链。 |

## 引擎、模型和缓存

- [`engine/locator.py`](../engine/locator.py)：按 `UTASYNCNEXT_ENGINE_ROOT`、`.engine`、`.venv` 顺序检查 `faster_whisper`、`demucs`、`torch`、`torchaudio`，但只做轻量路径探测。
- [`engine/status.py`](../engine/status.py)：不加载重模型的引擎状态和版本信息；`tools/check_engine_runtime.py --deep` 才会导入 Torch 检查 CUDA。
- [`engine/model_registry.py`](../engine/model_registry.py)：模型目录 `models_faster/`、完整 snapshot 检查（`config.json`、`model.bin`、tokenizer/vocabulary）。
- [`engine/model_downloader.py`](../engine/model_downloader.py)：HuggingFace 官方/HF Mirror/自定义源、代理和 dry-run 预检；默认不自动联网下载。
- [`engine/cache_env.py`](../engine/cache_env.py)：把 HuggingFace/XDG/Torch 缓存固定到 `.cache/`；输出任务名最长 60 字并附稳定 hash。
- `engine/hardware_profile.py`：GPU 显存分级；`engine/task_recovery.py`：失败日志、恢复报告和可复用 artifacts。
- `utils/ffmpeg_tools.py`：FFmpeg/FFprobe 检查、低优先级子进程、音频处理、ASS 硬字幕压制；`utils/network_proxy.py` 统一代理格式。

## 设置、输入、输出契约

`ui.main_window.MainWindow.collect_task_payload()` 传给 worker：

```python
{
    "media_path": "绝对路径/音频或视频",
    "lyrics": "参考歌词文本（可空）",
    "settings": {"...": "见 utils/config.py 的 DEFAULT_SETTINGS"},
}
```

关键设置键：`audio_type`、`hardware_mode`、`use_demucs`、`model_size`、`output_mode`、`ass_preset`、`aspect_ratio`、`fade_mode`、`hires_export`、`api_url`、`api_model`、`api_key`、`proxy_url`。真实用户设置在 `user_settings.json`，应保持本地且不提交；可提交模板是 `user_settings.example.json`。

输出根为 `app_root()/output/<task_base_name>/`，字幕在 `subtitles/`：原文 `*_极致打轴.srt`，翻译结果 `*_双语字幕.srt` 或 `*_纯中文字幕.srt`，ASS 通常为 `*_顶级特效.ass`。`done` artifacts 还可能包含 `source_srt`、`translated_srt`、`asr_diagnostics`、性能文本/JSON；失败时 `logs/` 下有错误日志和 `utasyncnext_recovery_*.json`。

## 工具索引

- 环境/边界：`tools/v3_guard.py`、`tools/check_no_secrets.py`、`tools/check_launcher_boundary.py`、`tools/check_engine_runtime.py`。
- 验证入口：`tools/verify_next_stack.py`（health/quick/network/model/pipeline/full/download）。
- 单项 smoke：`smoke_task_state.py`、`smoke_pipeline_worker.py`、`smoke_translator.py`、`smoke_ass_preview_window.py`、`smoke_style_workbench_preview.py`、`smoke_ffmpeg_preflight.py`、`smoke_task_recovery.py` 等，完整清单见 `tools/`。
- 回归与校对：`tools/regression_workspace.py`、`tools/subtitle_reviewer.py`、`tools/video_burn_regression.py`。
- 打包：`tools/package_launcher.ps1`、`tools/check_packaging_size.py`、`tools/link_dist_runtime.ps1`。
