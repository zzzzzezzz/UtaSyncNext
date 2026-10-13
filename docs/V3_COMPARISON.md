# UtaSyncNext 与 V3 对比

V3 现在是父目录里的只读参考，Next 是独立仓库和后续唯一的开发线。这份对比只讲可核实的事实差异，用来回答"Next 改进在哪，有没有还不如 V3 的地方"。

## 一句话总结

V3 把一整条流水线塞进 1177 行的 `app.py` 加 `core/` 三个模块，功能齐全但没有分层、没有自动化测试、没有 CI，而且公开仓库只提交了 11 个文件。Next 保留了 V3 的全部核心能力，把结构拆成能单独测试的模块，补上进程隔离、分阶段缓存、单实例、回归测试、CI 和文档，代价是引入"轻量 Launcher + 外置重引擎"这套新概念。

## 结构对比

| 维度 | V3 | UtaSyncNext |
| --- | --- | --- |
| 入口 | `app.py` 1177 行，UI、调度、压制、设置全在一个文件 | `app.py` 只做入口；UI 在 `ui/`，任务控制在 `controllers/`，管线在 `engine/pipeline_worker.py` |
| 模块规模 | `core/` 3 个模块 | core 16、engine 13、ui 28、utils 6、controllers 2 |
| 公开仓库 | 只跟踪 11 个文件；`ui/`、`controllers/`、`utils/` 从未提交 | 212 个文件，UI、工具和文档全部入库 |
| 重任务模型 | 线程直接改 Tk 控件，30 秒打印一次心跳 | 独立 worker 进程，消息协议回传，超时强杀进程树 |
| 依赖分层 | 一个环境装全部 | 轻量 `.venv` 负责 UI，外置 `.engine` 承载 Torch/Demucs/Faster-Whisper |
| 模型管理 | 目录自带，人工准备 | `engine/model_registry.py` 校验完整快照，缺失就在预检阶段停下并给安装指引 |
| 缓存 | 单一哈希指纹，粒度粗 | ASR 指纹 v7、翻译指纹 v8，分阶段、批次级、失败可续跑 |
| 硬件编码 | NVENC → VideoToolbox → QSV → AMF → CPU，共 5 个候选 | NVENC → CPU，共 2 个候选 |
| 单实例 | 无 | 命名互斥体；第二次启动把已有窗口带到前台，并写启动日志 |
| 自动化测试 | 无 | `tools/verify_next_stack.py` 七档 profile，另有 71 个 tools 脚本 |
| CI | 无 | `.github/workflows/verify.yml`，windows-latest 跑同一套检查 |
| 文档 | README 一份路线图 | README + `docs/` 15 篇，另有 `AGENTS.md`、`SAFETY.md` 边界文件 |
| 代码边界 | 无 | `tools/v3_guard.py` 指纹锁定 V3，`tools/check_no_secrets.py` 拦密钥 |
| 打包体积 | `dist` 259 MB（`_internal` 247 MB） | `dist\UtaSyncNext` 181 MB，其中 Launcher exe 只有 8.7 MB，重引擎和模型都不在里面 |

## 完整保留的 V3 能力

这一节说明 Next 不是功能缩水的重写。

- **Demucs 人声分离**，并且保留 V3 的分段策略。`core/generator.py` 仍按 `hardware_mode` 切段（`_demucs_plan`），另加 `engine/hardware_profile.py` 按显存自动选档，比 V3 的手动三档更省心。
- **Whisper 模型矩阵**，范围比 V3 更宽。Next 支持 `large-v2`、`large-v3`、`large-v3-turbo`、`medium`、`small`、`base`。
- **CUDA 回退**：`core/generator.py` 依次尝试 `float16`、`int8_float16`，都失败再退 CPU `int8`。
- **Hi-Res 输出**：`hires_export` 开关输出 MKV，并把音轨强制为 `-c:a flac -sample_fmt s32 -ar 48000`，与 V3 的 32-bit/48kHz FLAC 一致。
- **参考歌词对齐**、双语和纯中文输出、ASS 花字预设、FFmpeg 低优先级压制。
- **缓存命中跳过昂贵阶段**：改歌词不会强制重跑 ASR。

## Next 新补的工程能力

- **进程隔离**：Faster-Whisper、CTranslate2、Torch 和 Demucs 的 native/CUDA 状态都留在 worker 子进程里，取消时直接杀进程，UI 不会被拖死。V3 用线程，取消后容易残留显存。
- **批次缓存与续跑**：翻译按批次缓存，失败后从断点继续，完成 hash 只在所有批次成功后写入。
- **单实例与唤醒**：第二次双击不再静默退出；找不到窗口时也会闪烁任务栏并写 `launcher_startup.log`，配套排障写在 `TROUBLESHOOTING.md`。
- **可重复验证**：`verify_next_stack.py` 把编译、缓存隔离、离线模型策略、协议契约、UI 预览和回归工作区串成一条命令。本地 `quick` profile 当前 42 项全通过。
- **CI**：GitHub Actions 在 `windows-latest` 上跑同一套检查，克隆出去也能自证。
- **边界保护**：`v3_guard.py` 防止误写 V3，`check_no_secrets.py` 加 pre-commit 钩子防止密钥入库。
- **交接文档**：`docs/` 覆盖架构、模块、配置、管线、测试、排障、打包、扩展和决策，另有给 Trae/Cursor 的 `AI_HANDOFF.md`。

## Next 不如 V3 的地方

1. **硬件编码候选从 5 个减到 2 个**。V3 依次尝试 NVENC、VideoToolbox、QSV、AMF、libx264；Next 只有 NVENC 和 libx264。VideoToolbox 是 macOS 专用，对 Windows-only 的 Next 没意义；但 QSV（Intel 核显）和 AMF（AMD 显卡）对没有 NVIDIA 显卡的用户有实际价值，这一块目前是缺口。

   恢复成本很低：`utils/ffmpeg_tools.py` 的 `ENCODERS` 列表加两行即可，回退逻辑本来就是通用的。没有在冻结前直接改的原因有两个：这张卡不在本机可验证的范围内，而且部分 Intel 驱动下 QSV 会卡住而不是报错，加进回退链可能把"快速失败"变成"长时间无响应"。要不要补由你决定。

2. **多了一层安装概念**。V3 是"装完依赖直接 `python app.py`"；Next 第一次要先跑 `tools\bootstrap_venv.ps1`，需要跑真任务时再加 `-Engine`。换来的是 Launcher 启动快、模型不重复打包、依赖可以独立升级。

3. **研究脚本和生产工具混在一个目录**。`tools/` 里 71 个脚本有一部分是一次性研究或验收工具（`research_*`、`smoke_font_pair_ui_robot` 之类），不像 V3 那样干净。它们对复现证据有价值，但不是生产契约，`docs/STATUS.md` 已经标注了这一点。

## 结论

对当前的使用方式（Windows + NVIDIA、逐片调花字、需要可复现的回归证据）来说，Next 是 V3 的超集：能力没少，工程性和可维护性明显更强。唯一需要你拍板的能力缺口是 QSV 和 AMF 这两个硬件编码候选。