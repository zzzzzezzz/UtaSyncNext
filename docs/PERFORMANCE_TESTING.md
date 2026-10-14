# 性能、耐久和发布验收

复核日期：2026-10-14。

## 先给结论

当前不需要为了“性能还没做完”再重构管线。打轴阶段已经做过系统性的耗时、内存、缓存命中、取消和错误日志设计；本机审计目录里有 51 份真实任务性能报告，足以说明主要瓶颈在哪里（这些报告属于本机运行证据，不随仓库分发）：

| 观测 | 历史实测 |
| --- | --- |
| 冷 ASR 任务总耗时中位数 | 约 128 秒 |
| 模型加载耗时中位数 | 约 72 秒 |
| ASR 阶段耗时中位数 | 约 97 秒 |
| 一次 Demucs 长任务样本 | 约 1 倍实时，受素材和分段档位影响很大 |
| 进程峰值 RAM（有记录的报告） | 中位数约 935 MB，最高约 3.4 GB |
| 热缓存短片 | 曾实测约 0.7 秒级完成 |

这些数据表明时间主要花在首次加载模型、Demucs、ASR 和真实翻译 API；UI、字幕格式化和缓存命中路径不是当前主要瓶颈。启动历史也已经记录了冷启动差异。`tools/smoke_performance_metrics.py` 验证报告格式，不把本机速度伪装成跨机器基线。

当前性能设计包括：阶段计时和错误日志、ASR/翻译分阶段缓存、翻译批次续跑、Demucs 分段和失败重试、CUDA 到 CPU 的回退、取消后的进程回收，以及限制 OMP/MKL/OPENBLAS 并发。GPU 报告目前只记录显卡名称和总显存，不能当作 GPU 已用/峰值显存监控。

## 发布优先级

### P0：发布前必须完成

1. **缓存正确性修复**：音频缓存已增加媒体身份清单和 WAV 有效性检查；同名媒体被替换或缓存损坏时会失效。`health` 已覆盖该场景。
2. **重新构建 Launcher**：代码改动后重新生成 `dist\UtaSyncNext`，再跑边界审计、体积审计和启动 smoke。旧的 dist 不能代表当前源代码。
3. **本地闸门**：`health`、`quick`、`check_no_secrets.py`、`v3_guard.py verify` 全部通过。
4. **公开前隐私复核**：不要提交 `user_settings.json`、`output/`、API trace、媒体、模型或本机路径证据；API trace 即使已脱敏也属于敏感运行产物。

### P1：建议在公开仓库或首个正式包前做一次

1. **真实 API 翻译**：用一段短素材跑双语和纯中文，各人工检查断句、专有名词和错误恢复。它验证的是外部服务契约和产品质量，不是速度。
2. **干净环境首启**：在没有 `.engine`、模型、字体和个人设置的环境启动一次，确认提示清楚、不会偷偷联网下载。
3. **另一台 Windows 机器的成品 smoke**：把重建后的包复制过去，双击启动并完成一个短任务；若没有第二台机器，至少用没有 V3 参考目录的独立目录做一次启动和短缓存任务。
4. **取消与恢复抽查**：在短任务和压制阶段取消一次，确认进程退出、日志和可恢复工件仍在。

这些项目成本低，能直接降低“代码在开发机通过、用户包不能用”的风险，优先级高于继续调线程数。

### P2：发布后再做，不能阻塞当前 GitHub 入库

1. 固定一段短片建立可提交的冷/热性能基线，并在依赖或驱动升级后复测。
2. 真实 Intel QSV、AMD AMF 机器验证编码质量、码率和长视频行为；当前只有候选失败后继续回退的本机证据。
3. 10 分钟以上 Demucs 耐久、显存变化和取消压力测试。
4. 根据不同 CPU 动态调整 `libx264`/`OMP` 线程数；当前固定 4 线程是保守兼容选择，改动收益不确定。
5. 增加 GPU used/peak 采样，或把模型常驻到长期 worker。后者会削弱进程隔离和取消后的 CUDA 清理，现阶段投入产出比低。

## 需要运行的命令

```powershell
# 修改后默认闸门
.\.venv\Scripts\python.exe tools\verify_next_stack.py --profile health
.\.venv\Scripts\python.exe tools\verify_next_stack.py --profile quick
.\.venv\Scripts\python.exe tools\check_no_secrets.py
.\.venv\Scripts\python.exe tools\v3_guard.py verify

# 发布包
powershell -ExecutionPolicy Bypass -File .\tools\package_launcher.ps1 -Clean
.\.venv\Scripts\python.exe tools\check_launcher_boundary.py
.\.venv\Scripts\python.exe tools\check_packaging_size.py
.\.venv\Scripts\python.exe tools\smoke_icon_assets.py
```

真实管线和长任务是显式操作，示例：

```powershell
.\.venv\Scripts\python.exe tools\verify_next_stack.py `
  --profile pipeline --model large-v2 --media "E:\path\sample.mp4" --ass --timeout 600
```

运行后读取 `output/<任务名>/logs/utasyncnext_performance_*.json`。若要长期比较，应把素材、模型 snapshot、驱动、设置和冷/热状态一起记录；不要只比较一个总秒数。

## 不要把本机结果写成通用承诺

NVENC、QSV、AMF、CPU 编码速度取决于显卡、驱动、FFmpeg 构建、素材和参数。ASR/Demucs 的 CUDA 路径与视频最后一步的硬件编码是两套能力；能用 QSV/AMF 压制视频，不代表 Whisper 或 Demucs 能在对应 GPU 上获得同样的加速。真实 API 时延和质量也取决于服务商。文档中的历史数字只用于解释当前判断，不能作为所有电脑的 SLA。
