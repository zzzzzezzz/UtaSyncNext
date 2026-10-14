# UtaSyncNext 管线与缓存

复核日期：2026-10-14

UtaSyncNext 是本地桌面管线。UI 只收集媒体、歌词和设置，然后把 payload 交给独立的 multiprocessing worker；worker 通过 `log`、`status`、`progress`、`done`、`error` 消息回传状态。UI 轮询队列，取消时先设置事件，worker 超过约 2 秒仍未退出才终止进程树。

## 从点击“开始”到输出

1. `ui/main_window.py` 的 `collect_task_payload()` 读取当前媒体路径、歌词文本和侧边栏设置，并立即保存到 `user_settings.json`。
2. [`controllers/task_controller.py`](../controllers/task_controller.py) 做轻量引擎检查和 `ffmpeg`/`ffprobe` PATH 检查，通过后启动 `engine.pipeline_worker.run_pipeline_worker`。这一步不会导入 Torch、Whisper 或 Demucs。
3. worker 设置 Next 的缓存环境，定位 `.engine`/`.venv`，把重型环境的 `site-packages` 和脚本目录加入当前进程，再注册 Windows CUDA DLL 目录。媒体不存在、引擎模块不完整或外部 FFmpeg 不可用时，任务会在重型阶段前失败。
4. worker 规范化声学类型、Whisper 模型、Demucs 策略、输出模式和画幅策略，创建 `output/<任务名>/`。任务名是媒体 stem；超过 60 个字符时保留稳定的 8 位 MD5 后缀。
5. 先检查本地 Faster-Whisper 模型快照和 Demucs 权重（如果启用）。没有完整模型会拒绝自动下载并提示使用模型管理/下载工具。
6. 运行或复用 ASR。ASR 可能先用 FFmpeg 抽取 WAV，再按 Demucs 策略使用源音频或 `htdemucs_ft` 人声；Faster-Whisper 优先 CUDA，CUDA DLL/运行时失败会回退 CPU `int8`。输出原文 SRT 后进入后处理。
7. 若配置了 API Key，翻译器按批次调用 OpenAI-compatible Chat Completions；没有 Key 则保留原文 SRT 并跳过翻译。翻译成功后可生成 ASS，最后写性能报告并把工件路径发回 UI。

## 音频缓存身份

音频准备阶段另有一个 `audio_cache_manifest.json`。它记录绝对媒体路径、文件大小、修改时间和首尾边缘摘要；源 WAV 与 Demucs 人声 WAV 只有在媒体身份匹配且 WAV 结构有效时才会复用。同名文件被替换、旧版本缓存没有清单或缓存文件损坏时，会先清理音频/分离缓存，再重新提取。这个清单只保护音频缓存，不改变 v8 的字幕指纹契约。

## v8 ASR 指纹和 ASR 后处理

当前 ASR 指纹版本是 **v8**，写入：

```text
output/<任务名>/subtitles/<任务名>_打轴指纹.hash
```

指纹包含媒体边缘指纹、音频类型、模型大小和快照路径、是否跳过 Demucs、Demucs 硬件档、字幕计时策略、开头歌词清理策略、水印式误识别清理策略和现场音乐 VAD 策略。方形/竖屏还包含字幕密度预算。**参考歌词不参与 v8 ASR 指纹**：更换歌词不会强制重新跑昂贵的 Demucs/Whisper；歌词相关清理在复用或生成 SRT 后执行。任何早于 v8 的指纹（v6/v7，以及现场音乐 VAD 修复前的缓存）都会失配并重跑 ASR；`music_vad_policy` 就是这次修复留下的标记。

ASR 生成或命中缓存后都会执行以下检查（失败只记录诊断，不会让已有字幕消失）：

* 写入 `<任务名>_极致打轴_识别诊断.json`，记录 Faster-Whisper 证据或“仅缓存 SRT 文本”证据以及需人工复核的候选。
* 有强歌词锚点且证据足够时，开头/结尾窄范围内的明确水印式或低语音误识别可被清理；这是窄规则，不是通用“提高准确率”。
* 若提供歌词，写入 `<任务名>_极致打轴_歌词缺轴候选.json`（由 `opening_lyric_gap_report_path()` 决定），报告可能缺失的开头歌词空档。报告本身是 review-only。

## 歌词缺轴复听与独立预览的触发条件

检测到候选后，worker **最多检查前三个候选**，只有同时满足以下条件才会自动局部复听并尝试写预览：

* 这次运行实际生成了 ASR generator（命中 v8 SRT 缓存时 `generator` 为空，因此只写报告，不会自动复听）；
* `audio_type` 是 `live` 或 `live_soft`；访谈/播客不进入该分支；
* 候选未被 `automatic_lyric_gap_rescan_rejection_reason()` 拒绝；
* 建议复听窗口有效，且时长不超过 120 秒；
* 局部 Faster-Whisper 词级证据足以覆盖预期歌词行，并且严格预览不会与现有字幕轴重叠。

通过后会写入类似以下文件：

```text
<任务名>_歌词词级对齐_01.srt
<任务名>_歌词缺轴修复预览_01.srt
<任务名>_歌词缺轴修复预览_01_证据.json
```

这些是独立的 review 预览；日志会明确写“正式字幕未修改”。证据不足、候选过短/过长、匹配不足、重叠或局部复听异常时，保留原正式 SRT，只写报告和日志。该功能不承诺自动恢复所有漏听歌词，也不会把预览静默合并进正式字幕。

## v8 翻译指纹和批次缓存

当前翻译完成标记是 **v8**，写入：

```text
output/<任务名>/subtitles/<任务名>_翻译指纹.hash
```

v8 指纹包含媒体指纹、源 SRT 内容哈希、完整参考歌词、输出模式、`api_url`、用户选择的 `api_model` 和画幅文字预算。`api_model` 不会被代码悄悄替换；有参考歌词和无参考歌词时，当前选择模型分别作为 primary/reasoning 模型使用。

翻译器在同一目录维护 `<任务名>_极致打轴_翻译缓存.json`。批次缓存键还包含提示词策略、模型、布局预算、标题/专名策略和歌词 MD5，因此可以复用成功批次。v8 完成标记只有在所有批次成功且目标翻译 SRT 有效时才写入；批次失败会删除完成标记，但保留成功批次，下一次启动可以补失败部分。变更 API URL、模型、歌词、源 SRT、输出模式或布局会使完成标记失配，但不会无条件删除仍可复用的批次缓存。

API 客户端是 `openai.OpenAI`：请求超时 180 秒、SDK `max_retries=0`，每批使用 `chat.completions.create()`、`temperature=0.1`、`max_tokens=8192`。可选 `httpx.Client` 注入代理。翻译会检查 SRT 完整性、双语覆盖、说话人标签和跨轴歌词错位；局部重试仍无法满足规则时，该批次失败或拒绝进入缓存。

## 工件布局

```text
output/<任务名>/
  audio/
    <hash>_源音频.wav
    <任务名>_纯净人声.wav       # 启用 Demucs 时
    audio_cache_manifest.json  # 媒体身份和 WAV 缓存契约
    chunks/                    # Demucs 分段和日志
  subtitles/
    <任务名>_极致打轴.srt       # ASR 原文
    <任务名>_双语字幕.srt       # 双语翻译结果
    <任务名>_纯中文字幕.srt     # 纯中文字幕模式
    <任务名>_*.ass              # 生成 ASS 时
    <任务名>_极致打轴_api_trace.json # 翻译 API 调用记录（脱敏但仍属敏感运行产物）
    *_打轴指纹.hash
    *_翻译指纹.hash
    *_翻译缓存.json
    *_识别诊断.json
    *_歌词缺轴候选.json / *_歌词*预览*.srt / *_证据.json
  logs/
    utasyncnext_performance_*.txt/.json
    utasyncnext_error_*.txt
    utasyncnext_recovery_*.json
  <任务名>_硬字幕成品.mp4   # 普通压制
  <任务名>_硬字幕成品.mkv   # hires_export=true
```

ASS 生成不改变源 SRT。硬字幕压制由 FFmpeg 执行，按 `h264_nvenc`、`h264_qsv`、`h264_amf`、`libx264` 的顺序逐个尝试，任一候选失败就换下一个，最后一项是 CPU 软压；输出完成前会用 `ffprobe` 检查视频轨、音频轨和时长。中间音频/分离缓存可在 UI 清理，字幕和成品会保留。

