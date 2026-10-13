# UtaSyncNext 当前架构

本文按 2026-10-13 的源代码整理。应用是本地 Windows 桌面程序；没有浏览器前端、HTTP API 或服务端部署层。

## 真实启动链路

```text
run_next.ps1 / run_next.cmd
  -> .venv\Scripts\python.exe app.py
  -> mp.freeze_support()
  -> engine.cache_env.apply_next_cache_env()
  -> core.instance_lock.SingleInstanceLock
  -> 延迟导入 customtkinter 和 ui.main_window.MainWindow
  -> MainWindow 创建 Sidebar/Workspace/TaskController
  -> Tk mainloop
```

`app.py` 只在主 UI 启动后进入事件循环。`MainWindow` 的轻量状态检查使用 `engine.status.inspect_engine_lightweight()`，通过扫描 `site-packages` 和发行版元数据判断环境，不导入 Torch、Demucs 或 Faster-Whisper。

## 任务链路

```text
MainWindow.collect_task_payload()
  -> {media_path, lyrics, settings}
  -> TaskController.start_pipeline_task()
  -> multiprocessing.Queue + multiprocessing.Event
  -> multiprocessing.Process(run_pipeline_worker)
  -> engine.pipeline_worker.run_pipeline_worker
  -> Queue: log / progress / status / done / error
  -> Tk after(80ms) 批量轮询、合并日志、更新 Workspace
```

实际管线不在 `engine/worker_entry.py`；该文件只提供不加载重库的 `run_worker_demo`。真正的 worker 是 `engine.pipeline_worker.run_pipeline_worker`。

worker 首先准备 Next 引擎运行时：将 `.engine` 或 `.venv` 的 `site-packages` 加入当前子进程的 `sys.path`，把 `Scripts` 放到 `PATH`，并注册 Windows DLL 目录；然后才在任务需要时导入 `core.generator`、`core.translator` 和 ASS 相关模块。Demucs 命令有额外的 `demucs_runner.py` 子进程路径，但仍由 Next 引擎环境承载。

## 线程和进程边界

- Tk/CustomTkinter 控件只能由主线程访问。worker 通过 `Queue` 回传数据，`TaskController` 再在 Tk `after()` 回调中渲染。
- ASR、Demucs、翻译和长任务必须在 `multiprocessing.Process` 中运行。这样取消时可以回收 CTranslate2/CUDA 资源。
- FFmpeg 硬字幕压制是 `MainWindow` 创建的后台 `threading.Thread`，但线程不直接操作控件；进度和日志用 `self.after(0, ...)` 回到主线程。
- FFmpeg 自身由 `utils.ffmpeg_tools.safe_run/safe_popen` 启动，并在 Windows 使用 `creationflags=0x08000000` 隐藏黑框。
- worker 的 stdout/stderr 使用 `WorkerStdoutRedirector`，必须保留 `write`、`flush`、`isatty`、`fileno`、`encoding`、`closed` 等接口。

取消任务时先设置 `cancel_event`；控制器等待约 2 秒，仍未退出才调用 `kill_process_tree`。UI 关闭也会先请求取消，并最多等待约 6 秒。

## 消息协议

定义在 `engine/protocol.py`：

| type | 必有字段 | 用途 |
| --- | --- | --- |
| `log` | `text` | 过程日志；控制器会合并一轮收到的多条日志 |
| `progress` | `progress`、`text` | 0 到 1 的可视化进度 |
| `status` | `progress`、`text` | 阶段状态，语义上与进度更新分开 |
| `done` | `text` | 成功或用户取消；可选 `artifacts`、`summary` |
| `error` | `text` | 失败信息；控制器会写错误日志/恢复报告 |

扩展协议时要同时修改 `engine/protocol.py`、`controllers/task_controller.py` 和相关 smoke；不要让 worker 直接调用 Tk。

## UI 状态机

`ui.task_state.TaskSnapshot` 是无 Tk 的不可变状态契约：

```text
EMPTY -> READY -> PREFLIGHT -> RUNNING -> SUCCEEDED
                         \-> FAILED
RUNNING -> CANCELLING -> CANCELLED
```

`QUEUED` 已在状态枚举中保留给串行批量队列。当前 controller 是单任务，不会并行抢显存。所有布局都应通过 `TaskSnapshot` 渲染，而不是在 worker 中写控件。

## 安全和资源边界

- V3 父仓库只读；`tools/v3_guard.py` 在 Next 修改前后校验父目录指纹。
- UI、Launcher、模型、引擎和缓存分层；重型包不进入 Launcher。
- 取消/失败也要保留可读日志和已有可复用产物，避免把半成品标记为完成。
- API Key 不能写入日志、报告或提交；错误报告应使用现有脱敏逻辑。

## 改动时先看哪里

任务生命周期看 `controllers/task_controller.py`；阶段和缓存看 `engine/pipeline_worker.py`；模型/引擎发现看 `engine/locator.py`、`engine/status.py`、`engine/model_registry.py`；UI 状态看 `ui/task_state.py`；外部命令看 `utils/ffmpeg_tools.py`。不要依据父目录 V3 的 `controllers/pipeline_task.py` 推断 Next 行为。
