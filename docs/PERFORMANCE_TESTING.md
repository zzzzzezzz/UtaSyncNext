# 性能与耐久测试

这份文档说明 Next 的性能埋点现状、为什么还值得补一个基线，以及发布前剩下哪些只能在真机上做的验证。

## 现在有什么

- 每次真实任务都会写 `output/<任务名>/logs/utasyncnext_performance_*.txt` 和 `.json`，包含各阶段耗时、进程内存、缓存命中情况和网络方式（`utils/performance_metrics.py`）。
- 启动耗时记录在 `performance/startup_history.jsonl`，该目录被 Git 忽略。
- `tools/smoke_performance_metrics.py` 只验证报告能正确写出和渲染，不测真实速度。

也就是说：埋点已经齐全，但还没有基线。

## 为什么值得补

没有基线就没有"变慢了"的判断依据。Torch、CUDA、CTranslate2、FFmpeg 和 nvenc 任何一次升级都可能让某个阶段慢一半，而默认验证不跑长任务，这类退化不会被现有测试发现。

## 建议的最小基准方案

固定 2 到 3 段样片长期复用，不要每次换素材：

| 样片 | 用途 | 关注指标 |
| --- | --- | --- |
| 1 分钟短片（有歌词） | 快速回归 | 冷启动耗时、ASR 实时率、总时长 |
| 10 分钟 Live（嘈杂、有伴奏） | 长任务与 Demucs | Demucs 耗时比、分段数、显存峰值、总时长 |
| 一段开启 Hi-Res 的成品 | 压制路径 | NVENC 与 libx264 耗时对照、MKV + FLAC 输出校验 |

每次记录：ASR 实时率（音频时长 ÷ 打轴耗时）、Demucs 耗时比、模型加载耗时、冷缓存与热缓存的总时长差、压制耗时、进程峰值内存。

判定阈值建议：同一素材、同一设置下，关键阶段耗时不超过上次基线的 1.2 倍；超过就先查依赖版本和显卡驱动，不要先怀疑业务代码。

## 具体怎么跑

```powershell
# 单段真实管线，会加载本地模型并写性能报告
.\.venv\Scripts\python.exe tools\verify_next_stack.py --profile pipeline --model large-v2 --media "E:\path\sample.mp4" --ass

# 带 Demucs 的耐久版，超时放宽
.\.venv\Scripts\python.exe tools\verify_next_stack.py --profile pipeline --model large-v2 --media "E:\path\live.mp4" --demucs --ass --timeout 3600
```

跑完到 `output/<任务名>/logs/` 读 `utasyncnext_performance_*.json`，把关键字段抄进一张长期表格。建议放在 `performance/baselines.md`：该目录已被忽略，不会污染仓库。

## 明确不放进默认 CI 的

真实 API 调用、模型下载、长视频、Demucs 长分离、CUDA 驱动升级、删除缓存的破坏性用例。理由写在 [`TESTING.md`](TESTING.md)。

## 发布前剩余测试清单

- [ ] **真实 API 翻译**：一段真实视频跑通双语和纯中文，人工检查断句、专有名词和文学质量（`tools\smoke_real_translation.py`，或 `smoke_full_pipeline.py --use-settings-api`）。
- [ ] **干净机器首启**：在没有 `.engine`、没有 `models_faster`、没有 `fonts/`、没有 `user_settings.json` 的机器上按 README 走一遍，确认提示可读、不崩溃。
- [ ] **长视频耐久**：10 分钟以上加 Demucs，确认显存不爆、进度不卡、取消可用。
- [ ] **打包成品实机验证**：`dist\UtaSyncNext` 拷到另一台机器，双击能起、能跑完一个短任务。
- [ ] **性能基线**：按上面的方案记录第一份基线。

前三项在开发机上就能做，后两项需要一台干净机器或另一台电脑。这五项都属于"发布前应该做一次"的性质，不必写进每次提交都要跑的默认验证。