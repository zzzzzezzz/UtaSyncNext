# Trae / Cursor 交接提示

把下面的提示复制给新的代码工具，并将工作目录直接设为 `UtaSyncNext`：

```text
这是 UtaSyncNext，一个 Python + CustomTkinter Windows 桌面应用，不是 Next.js/React 项目。

先阅读：README.md、AGENTS.md、SAFETY.md、docs/ARCHITECTURE.md、docs/MODULE_MAP.md，以及本次目标模块对应的 docs/CONFIGURATION.md 或 docs/PIPELINE.md。当前真实管线入口是 engine.pipeline_worker.run_pipeline_worker；engine/worker_entry.py 只有 demo。

工作边界：只修改当前 UtaSyncNext 独立 Git 仓库。父目录 UtaSync 是只读的 V3 参考，禁止在那里写入、格式化、测试、打包、git add 或 git commit。开始前运行 .venv\Scripts\python.exe tools\v3_guard.py begin，结束后运行 check_no_secrets.py 和 v3_guard.py verify。

实现前先说明：要改哪些文件、现有契约是什么、会跑哪些本地 smoke。保持 Windows 无黑框的 creationflags、ASR/Demucs/翻译的 multiprocessing.Process、stdout/stderr 伪装接口、Tk 主线程更新、取消后的子进程回收、缓存指纹和输出路径兼容。不要未经验证地把 worker 改成线程、把重库导入放回 Launcher、或在任务中隐式联网下载模型。

完成后报告改动、测试命令和任何未验证的真实 API/模型/长视频限制；不要输出 API Key 或 user_settings.json 内容。
```

工具不会因为文件名就必然读取所有项目文档；把本提示和目标模块路径一起提供，仍以代码和测试结果为准。不要为 `.cursor/rules` 或 `.trae/rules` 复制一套会漂移的完整规则，短入口应指向这些 canonical 文档。
