# UtaSyncNext 验证指南

测试都在 `UtaSyncNext` 根目录执行。默认不联网、不下载大模型、不调用真实翻译 API、不跑长视频和 Demucs 长任务。

## 测试矩阵

| profile | 命令 | 覆盖 | 外部副作用 |
| --- | --- | --- | --- |
| `health` | `\.venv\Scripts\python.exe tools\verify_next_stack.py --profile health` | 编译、缓存隔离、离线模型策略、协议/缓存/回归契约，以及大量本地 smoke、字体/样式预览和 Tk 小窗口 | 可能创建 `test_logs` 和临时输出；不跑真实翻译 API，不执行 UI import 检查 |
| `quick` | `\.venv\Scripts\python.exe tools\verify_next_stack.py --profile quick` | `health` 加双语/纯中文假 API translator smoke、UI imports | 本地 smoke；需要轻量依赖 |
| `network` | `... --profile network --model small --source official --proxy 10808` | HuggingFace 源清单预检、文件数和大小 | 联网，不下载完整模型 |
| `model` | `... --profile model --model small` | 本地 snapshot 完整性和 Faster-Whisper 加载 | 加载本地模型，消耗 CPU/GPU |
| `pipeline` | `... --profile pipeline --model small --media "..." --ass --timeout 600` | 真实 worker、音频、ASR、SRT、可选 ASS、缓存 | 使用真实媒体和本地模型；启用 `--demucs` 会消耗更多资源 |
| `full` | `... --profile full --model small --media "..." --ass` | network + model + pipeline | 联网、模型加载、真实媒体 |
| `download` | `... --profile download --model large-v2 --confirm-download` | 真实 HuggingFace 下载和完整性校验 | 会下载数 GB；必须明确加 `--confirm-download` |

`health` 名称表示不执行真实翻译/大模型管线，并不表示完全不触碰 Tk 或 FFmpeg：当前 runner 会运行 `smoke_font_preview_window.py`、`smoke_style_workbench_preview.py`、图标、回归和其它本地 smoke。需要只验证语法时使用：

```powershell
\.venv\Scripts\python.exe -m compileall app.py controllers core engine ui utils tools
```

## CI（GitHub Actions）

`.github/workflows/verify.yml` 在 `windows-latest` 上跑和本地同一套检查：

```powershell
python tools/verify_next_stack.py --profile health --skip network-proxy,preset-catalog --json-report test_logs/health.json
```

只排除两项，因为它们的失败取决于本机环境而不是代码：

- `network-proxy` 需要本机已安装 Next 引擎（`.engine`）才能走到代理预检提示；
- `preset-catalog` 用 `fonts/` 目录里的字体渲染，而该目录不进 Git。

`--skip` 只影响这一次运行；本地完整执行 `--profile health` 时它们照常跑。CI 里也不调用
`tools/v3_guard.py`：克隆出来的检出旁边没有 V3 参考目录，guard 会打印跳过说明并以 0 退出。

## 改动后的推荐顺序

```powershell
\.venv\Scripts\python.exe tools\v3_guard.py begin
\.venv\Scripts\python.exe tools\verify_next_stack.py --profile health --json-report test_logs\health.json
\.venv\Scripts\python.exe tools\check_no_secrets.py
\.venv\Scripts\python.exe tools\v3_guard.py verify
```

修改 worker、缓存或 UI 状态时，再追加对应专题 smoke，例如：

```powershell
\.venv\Scripts\python.exe tools\smoke_task_state.py
\.venv\Scripts\python.exe tools\smoke_pipeline_worker.py
\.venv\Scripts\python.exe tools\smoke_task_recovery.py
\.venv\Scripts\python.exe tools\smoke_preset_store.py
\.venv\Scripts\python.exe tools\smoke_instance_activation.py
\.venv\Scripts\python.exe tools\smoke_translation_guardrails.py
```

真实 API 翻译是显式选择：只在本地 `user_settings.json` 已配置并确认额度后运行 `tools\smoke_real_translation.py` 或 `smoke_full_pipeline.py --use-settings-api`。长视频、large-v2/large-v3 下载、Demucs 长任务、删除缓存测试和打包体积测试都应单独安排。

## 回归证据

`test_workspace` 采用三层证据：`guardrail`（已定位问题）、`anchor`（日常短片）、`milestone`（长 Live/电台）。自动稿只能验证结构、时间轴、异常文本和性能；只有 `human_approved` 才能作为翻译/听感质量基准。运行：

```powershell
\.venv\Scripts\python.exe tools\regression_workspace.py report
```

字幕校对器可同时加载媒体、原文和译文 SRT：

```powershell
\.venv\Scripts\python.exe tools\subtitle_reviewer.py --media "E:\path\sample.mp3" --source-srt "E:\path\source.srt" --translated-srt "E:\path\translated.srt"
```

## 发布前还要做一次的验证

未自动化的长任务、真实 API 质量检查、干净机器首启、打包成品实机验证，以及性能基线的建立方式，都写在 [`PERFORMANCE_TESTING.md`](PERFORMANCE_TESTING.md)。它们属于"发布前做一次"的性质，不必写进每次提交都要跑的默认验证。

## 不要把这些测试写成默认 CI

- 真实 API 请求、真实模型下载和大模型加载。
- 长视频、Demucs 长分离、CUDA 驱动升级。
- 删除 `output`/缓存的破坏性用例。
- 依赖本机字体注册表或个人媒体路径的 UI 证据。

这些测试需要在报告中记录输入媒体、设置、模型 snapshot 和人工结论，避免把本机状态误写成通用结论。
