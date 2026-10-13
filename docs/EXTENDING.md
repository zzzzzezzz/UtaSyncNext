# 二次开发指南

先阅读 [`README.md`](../README.md)、[`AGENTS.md`](../AGENTS.md) 和 [`ARCHITECTURE.md`](ARCHITECTURE.md)，再只打开本次目标模块。所有改动都限定在 `UtaSyncNext`，开始前执行 `tools\v3_guard.py begin`。

## 添加或修改设置

1. 在 `utils/config.py` 更新 `DEFAULT_SETTINGS` 和必要的旧值归一化。
2. 在对应 UI panel（`ui/basic_settings_panel.py`、`ui/api_settings_panel.py`、`ui/visual_settings_panel.py`）读写同一个键。
3. 确认 `MainWindow.save_current_settings()` 会持久化它；不要把密钥写入日志或示例文件。
4. 如果设置影响 ASR、翻译或 ASS，加入对应缓存指纹或明确证明它不影响该阶段。
5. 更新 `user_settings.example.json`（只能放空 Key 和安全示例值）及 [`CONFIGURATION.md`](CONFIGURATION.md)。

## 添加 UI 功能

- 控件只在主线程创建和更新；后台结果用 `after()` 回到 Tk。
- 任务状态通过 `TaskSnapshot`，不要把自定义状态塞进 worker 全局变量。
- 新窗口要复用现有主题、图标、标题栏和单实例/恢复行为。
- 长日志必须沿用 controller 的批量合并和节流，不要每行触发一次完整重绘。
- 使用 `tools\smoke_task_state.py`、相应 UI smoke 或小型无窗口测试证明状态转移。

## 修改 worker 或消息协议

1. 在 `engine/protocol.py` 定义消息构造器或新字段。
2. 在 `engine.pipeline_worker.py` 发送，并在 `controllers/task_controller.py` 处理。
3. 保证 `done`/`error` 路径都留下状态、日志和已有可复用结果。
4. 取消必须检查 `cancel_event`；不能把 ASR/Demucs 改成线程。
5. 为新字段增加 smoke 或更新 `tools/verify_next_stack.py` 的纯本地检查。

## 修改模型、翻译或缓存

- 模型必须通过 `engine.model_registry` 的完整 snapshot 校验；任务中禁止隐式联网下载。
- `core.translator.SubtitleTranslator` 使用 OpenAI 兼容接口；provider 的 base URL、模型和代理来自设置。
- ASR 指纹和翻译指纹必须区分阶段，先阅读 [`PIPELINE.md`](PIPELINE.md) 的版本和组成，不要简单加入“所有设置”。
- 批次缓存和完成标记必须在失败时保留可复用批次，同时不把部分结果标成完整。
- 修改字幕清理/歌词补轴时，先跑对应 `smoke_lyric_*` 和回归工作区；生产正式字幕与严格修复预览要分开。

## 修改 ASS、字体或预设

内置样式在 `core.ass_maker.py`，映射/布局在 `core.ass_config.py` 和 `core.subtitle_policy.py`，字体导入和搭配在 `ui.font_catalog.py`。保留 `fonts` 目录外置策略和用户预设的 `__meta__` 删除/改名记录。运行：

```powershell
\.venv\Scripts\python.exe tools\smoke_preset_store.py
\.venv\Scripts\python.exe tools\smoke_font_pair_catalog.py
\.venv\Scripts\python.exe tools\smoke_style_workbench_preview.py
```

## 提交前最小清单

```powershell
\.venv\Scripts\python.exe tools\verify_next_stack.py --profile health
\.venv\Scripts\python.exe tools\check_no_secrets.py
\.venv\Scripts\python.exe tools\v3_guard.py verify
git status --short
```

不要提交 `.venv`、`.engine`、模型、媒体、`output`、日志、`user_settings.json` 或本地歌词。任何需要修改父 V3 的请求都要单独停止并取得明确授权。
