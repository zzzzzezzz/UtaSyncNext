# UtaSyncNext 与 V3 对比

复核日期：2026-10-14。V3 的版本锚点是父目录只读仓库的 `HEAD=0d7e5dd`（`feat: V1.0 核心全链路跑通存档`）；Next 以本次审计工作树和当前测试结果为准。不要用父目录工作树里后来被替换或未提交的文件行数代替 V3 基线。

这份对比只记录可以从两个仓库和当前代码核实的差异。UtaSyncNext 的 “Next” 是产品下一代管线名称，不是 Next.js/React/Node.js 项目。

## 规模与入口

| 维度 | V3（锁定 `0d7e5dd`） | UtaSyncNext（本次审计工作树） |
| --- | --- | --- |
| 入口 | `app.py`；该版本文件 1,061 行，UI、任务调度和压制代码集中在 Tk 文件内 | [`app.py`](../app.py) 负责启动、缓存环境和单实例；UI、controller、engine、core、utils 分层 |
| Git 跟踪规模 | 11 个文件；核心入口由 `app.py` 调用 `core/` | 独立仓库；UI、controller、engine、core、工具和文档均纳入版本控制 |
| UI 调度 | 使用 Tk `after()` 轮询队列/刷新界面，也从线程执行长任务 | 主线程用 `after()` 轮询 multiprocessing worker 消息；硬字幕压制仍由后台线程执行并用 `after()` 回到 Tk |
| 重任务隔离 | 长流程主要在线程中运行；FFmpeg 压制在后台线程 | ASR/Demucs/翻译放进独立 worker 进程；压制保持独立后台线程 |
| 环境 | 单一运行环境，直接由 V3 入口加载依赖 | `.venv` 轻量 UI + `.engine` 外置重引擎；模型、缓存和输出均独立于 launcher |
| 单实例 | 锁定版本没有 Next 的命名互斥体和窗口唤醒路径 | 命名互斥体；第二次启动先尝试按窗口标题唤醒，找不到匹配窗口时显示“结束残留进程/稍后重试”的恢复提示，并写 `launcher_startup.log` |
| 验证/边界 | 没有 Next 的仓库级 guard 和 CI 约束 | `tools/v3_guard.py`、密钥扫描、Windows CI 和大量 smoke/回归工具 |

## 缓存与恢复

V3 在 `app.py` 中已经有**分离的 ASR 指纹和翻译指纹**：分别是 `<base>_打轴指纹.hash` 与 `<base>_翻译指纹.hash`，并维护翻译批次缓存。V3 会根据声学/分离/模型设置决定是否跳过已有打轴；当源字幕需要重新生成时，会让翻译缓存失效。V3 的指纹内容较粗，主要由 UI 当前状态拼接生成，媒体内容和模型快照等运行身份没有纳入同等细度的策略。

Next 保留这两个阶段的边界并把策略显式化：

* ASR 指纹 **v7** 包含媒体边缘指纹、音频类型、模型大小与 snapshot、Demucs/硬件策略、计时/清理策略和窄画幅密度；参考歌词不参与 ASR 指纹，因此修正歌词通常不需要重新跑 ASR。
* 翻译完成指纹 **v8** 包含媒体、源 SRT 内容哈希、歌词、输出模式、API URL、用户选择的模型和布局预算。
* 翻译批次保存在 JSON 中；部分批次失败会删除“完成”标记但保留已成功批次，下一次运行可以补失败批次。只有全部批次成功且最终 SRT 有效时才写完成标记。
* 出错时会生成普通日志和恢复 JSON。恢复报告只说明“可复用原文打轴/音频缓存/翻译批次”的范围，不把任意半成品承诺为可安全续跑。

因此，Next 的变化是更细的指纹和更明确的恢复边界，不是 V3 从来没有缓存或 hash。

## 核心工作流覆盖

两条线都覆盖同一条核心工作流：媒体输入 → 音频准备/可选 Demucs → Faster-Whisper 打轴 → 可选参考歌词和大模型翻译 → SRT/ASS → FFmpeg 硬字幕成品。Next 额外把这些阶段拆成可独立验证的模块和 worker 协议，并把模型、缓存、诊断、错误恢复和打包边界写成仓库内契约。

当前可以直接从代码确认的覆盖包括：

* Demucs 人声分离、跳过分离、按显存选择分段档位；Demucs 权重缺失时在分离前停止，不隐式下载。
* Faster-Whisper `large-v2`、`large-v3`、`large-v3-turbo`、`medium`、`small`、`base` 的本地模型快照检查；CUDA 失败后 CPU `int8` 回退。
* 中日双语/纯中文输出、参考歌词局部上下文、歌词缺轴候选检查、ASR 诊断、ASS 预设和字体导入。
* 普通 MP4 和 Hi-Res MKV + FLAC 输出；压制结果通过 FFprobe 检查轨道、文件有效性和时长。

这些能力的实际可用性仍取决于 Windows、FFmpeg/FFprobe、`.engine`、模型快照、Demucs 权重、GPU 驱动和 API 配置；文档中的“覆盖”不等于每种硬件和外部服务都已做过真实验收。

## 硬件编码回退

当前 Next [`utils/ffmpeg_tools.py`](../utils/ffmpeg_tools.py) 的候选顺序是：

1. `h264_nvenc`：`fast`、`yuv420p`、`cq 22`；
2. `h264_qsv`：`fast`、`global_quality 22`、显式 `nv12`；
3. `h264_amf`：`quality=speed`、`rc=cqp`、I/P/B QP 22、`yuv420p`；
4. `libx264`：`superfast`、`crf 22`、`threads 4`。

V3 在其 `app.py` 中的候选表还写有 `h264_videotoolbox`；该编码器是 macOS 专用，Windows 发布的 Next 不列它。V3 的 QSV/AMF 参数来自较旧的候选实现，Next 对 QSV 显式指定 `nv12`，对 AMF 使用当前 FFmpeg 可识别的 `rc=cqp`，避免把旧的 `rc=vbr` 当作通用参数。

当前证据范围需要分开看：

* 已有开发机基准记录过 60 秒 1080p + ASS 的 NVENC 约 7.7 秒、`libx264`（上述参数）约 18.2 秒；该数字是特定 NVIDIA 开发机、特定素材和设置的历史基线，不是所有机器的承诺，也没有纳入默认 CI。
* 当前环境的 FFmpeg 2025-09-25 full build 中，NVENC 和 `libx264` 的短 lavfi 探针退出码为 0；QSV 因没有可用 MFX 会话失败，AMF 因缺少 `amfrt64.dll` 失败。两者参数没有解析错误，但这不是 Intel/AMD 成功编码测试。
* 尚未在真实 Intel 核显或 AMD 显卡上验证成功编码、质量/码率、驱动组合、多 GPU 选择、长视频和 Hi-Res 输出。某些驱动或裁剪版 FFmpeg 可能在初始化阶段失败，甚至长时间无进展；代码目前没有对单个候选设置独立的无进展超时。

ASR/Demucs 的 GPU 加速依赖 CUDA，与最后一步的视频硬件编码是两条路径。QSV/AMF 能压制视频并不意味着 Faster-Whisper 或 Demucs 在 Intel/AMD GPU 上获得同等加速；ASR 的 CPU 回退仍可能显著变慢。

因此，回退链的结论是“候选已恢复且非零退出时能继续尝试下一项”。NVIDIA 开发机的失败耗时只能代表该机，不能据此承诺其他硬件和驱动上的初始化速度或无挂起行为。

## 工程增强与代价

Next 的主要工程增强是：

* worker 进程隔离 native/CUDA 状态，取消时可以终止进程树；
* ASR/翻译分阶段 hash、翻译批次缓存和脱敏恢复报告；
* 模型完整性检查、缓存目录隔离、单实例窗口唤醒和启动证据；
* 可重复的 health/quick/pipeline 验证、Windows CI、打包边界审计和 V3 只读 guard；
* 给 Trae/Cursor 的模块地图、配置、管线、测试、排障、打包和扩展文档。

代价和限制也要保留在预期内：首次安装要分别准备 `.venv` 与 `.engine`，模型和 Demucs 权重占用外部磁盘；`tools/` 中同时包含生产检查、smoke、研究和验收脚本；真实 API、长视频、真实 Intel/AMD 编码和干净机器打包启动仍需单独验证。

## 结论

以 V3 `0d7e5dd` 为基线，Next 覆盖了当前核心工作流，并把运行隔离、阶段缓存、恢复说明、验证和发布边界做成了更清晰的工程结构。结论限于“核心工作流覆盖 + 工程增强”；全面的硬件和外部服务验收仍待补充。V3 仍是只读行为参考；二次开发应以 Next 当前代码、`docs/` 和实际环境检查结果为准。
