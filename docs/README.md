# 文档入口

这里是 UtaSyncNext 的文档索引，用来把“软件怎么用”和“代码怎么接手”分开。项目是 Python + CustomTkinter Windows 桌面应用，不是 Next.js 或 Web 服务。

## 按目标阅读

| 目标 | 建议顺序 |
| --- | --- |
| 软件使用者 | 根目录 [`README.md`](../README.md) → [`OUTPUT_FILES.md`](OUTPUT_FILES.md) → [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) |
| 维护者快速接手 | [`DEVELOPMENT.md`](DEVELOPMENT.md) → [`ARCHITECTURE.md`](ARCHITECTURE.md) → [`MODULE_MAP.md`](MODULE_MAP.md) |
| 二次开发 | [`EXTENDING.md`](EXTENDING.md) → [`CONFIGURATION.md`](CONFIGURATION.md) → [`PIPELINE.md`](PIPELINE.md) → [`DECISIONS.md`](DECISIONS.md) |
| 对比 V3、了解取舍 | [`V3_COMPARISON.md`](V3_COMPARISON.md) → [`DECISIONS.md`](DECISIONS.md) |
| 修改后验证 | [`TESTING.md`](TESTING.md) → 根目录 [`TEST_PLAN.md`](../TEST_PLAN.md) → [`PERFORMANCE_TESTING.md`](PERFORMANCE_TESTING.md) |
| 打包发布 | [`PACKAGING.md`](PACKAGING.md) → [`SECURITY.md`](SECURITY.md) → [`STATUS.md`](STATUS.md) |
| 交给 Trae/Cursor | [`AI_HANDOFF.md`](AI_HANDOFF.md) → [`ARCHITECTURE.md`](ARCHITECTURE.md) → [`EXTENDING.md`](EXTENDING.md) |

## 查找入口

- 启动入口：`app.py`
- 主窗口和 UI：`ui/`
- 任务控制：`controllers/`
- 实际长任务：`engine/pipeline_worker.py`
- ASR、翻译和字幕处理：`core/`
- 本地验证和 smoke：`tools/`

普通用户读完前两项即可；不要为了使用软件阅读开发和发布文档。代码、测试和当前配置优先于历史计划文档；涉及父目录 V3 时先阅读 [`AGENTS.md`](../AGENTS.md) 和 [`SAFETY.md`](../SAFETY.md) 的只读边界。

复核日期：2026-10-14。
