# 已落地架构决策

## 独立 Next 仓库，V3 只读

V3 继续作为稳定参考，Next 有自己的 Git 仓库和运行资产。`tools/v3_guard.py` 用父目录文件指纹阻止误写/误提交，避免重构过程中把两个产品线混在一起。

## 用 `multiprocessing.Process` 承载重任务

Faster-Whisper、CTranslate2、Torch 和 Demucs 都可能持有大量 native/CUDA 状态。取消长任务时结束子进程比强行停止线程更能保证资源回收，因此 controller 只创建一个 worker 进程，并保留超时后的进程树终止。

## Launcher 与 engine/model 外置

UI 需要快速启动，Torch、Demucs、Faster-Whisper、CUDA DLL 和模型体积大且变化频繁，所以 Launcher 不收集它们。worker 在任务开始后挂载 `.engine` 的 `site-packages`；模型位于 Next 的 `models_faster`，缓存位于 `.cache`。

## 默认先跳过 Demucs

快速得到可靠 ASR 结果比每次启动都等待人声分离更适合默认工作流。用户仍可显式选择 Demucs；启用时必须先通过权重预检，缺权重就停止并提示，不隐式下载。

## 缓存按阶段分开

ASR 指纹为 v7，包含媒体身份、声学模式、模型和 snapshot、Demucs/硬件策略及字幕布局策略，但不包含参考歌词；这样修正歌词不会强制重跑昂贵 ASR。翻译指纹为 v8，包含媒体、源 SRT 内容、歌词、输出模式、API URL、模型和布局。批次缓存可以在失败后续跑，完成 hash 只有所有批次成功才写入。

## 正式字幕和歌词修复预览分离

歌词缺轴检查可以对有限窗口做词级复听并生成独立预览及证据；证据不足时保留正式 SRT，不自动替换生产字幕。这样人工可以审查修复候选，回归工作区也能保留原始结果。

## UI 状态由不可变快照驱动

`TaskSnapshot` 不依赖 Tk，主界面和后续布局可以共享同一任务语义。worker 只发送协议消息，controller 负责把消息转换为状态、日志和产物，避免后台代码直接操控控件。

## 不做批量任务队列

每个视频的预设、花字、字体和歌词处理方式都不一样，逐片调整是这条流水线的常态。把任务串成队列会让"这一片单独改什么"变成队列里的特例，收益低于复杂度，因此决定不实现。`TaskPhase.QUEUED` 只是状态枚举里的保留值，当前 controller 仍然一次只跑一个任务，也不并发抢显存。
## 压制编码器逐级回退，不允许没有 N 卡就直接掉到软压

硬字幕压制按 `h264_nvenc` → `h264_qsv` → `h264_amf` → `libx264` 的顺序逐个尝试，任一步成功就返回。V3 用的是同一套思路；Next 早期只保留 NVENC 和 CPU 两项，等于让 Intel/AMD 机器直接落到软压，所以补回来。

几个不该随手改回去的细节：

- `h264_qsv` 只接受 `nv12` / `qsv` 像素格式，必须显式写 `-pix_fmt nv12`。
- `h264_amf` 用 `-rc cqp`。V3 写的 `-rc vbr` 在新版 ffmpeg 里已改名（现在叫 `vbr_peak`），照抄老参数会直接失败。
- VideoToolbox 是 macOS 专用，本应用只发布 Windows，因此不列入候选。
- `libx264` 固定在最后，`-threads 4` 是沿用 V3 的参数；这个上限意味着换更强的 CPU 也不会让软压明显变快。
- 失败原因按候选名写进日志，不再笼统地说"显卡失败改用 CPU"。

实测数据和代价见 [`V3_COMPARISON.md`](V3_COMPARISON.md)。