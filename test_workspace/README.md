# UtaSyncNext 回归验收工作区

这里把长期测试定义与产品代码、日常输出分开保存。目标不是让脚本替人判断字幕好坏，而是让每次结论都能回到同一份素材、设置和人工标准。

## 边界

- 原始媒体、v3 输出和人工确认字幕只读使用。
- 大体积媒体只记录路径，不复制进仓库。
- `cases.local.json`、报告和运行产物只保留在本机，并由 Git 忽略。
- v3 自动输出属于历史证据，不等于人工确认答案。
- 每轮修改先跑短锚点；长 Live 只在阶段性版本做耐久测试。

## 三层测试集

- `guardrail`：已经定位过的具体问题，例如窄画幅密度、假人名和时间轴完整性。
- `anchor`：3 到 10 分钟的代表短片，用于日常比较柔和演唱、高速摇滚、歌词参考和访谈。
- `milestone`：一到两小时的 Live 或电台，只用于阶段性验证内存、断点续跑和长任务稳定性。

## 参考级别

- `v3_auto`：未经修改的 v3 自动输出。
- `next_auto`：未经修改的 Next 自动输出。
- `human_approved`：用户已经审看并认可的字幕。

只有 `human_approved` 能作为质量基准。自动稿只适合检查结构变化、时间轴完整性、异常前缀和性能，不自动判定翻译优劣。

## 快速听轴校对

音频或长视频不需要先压制成片。使用本地校对器同时载入媒体、原文 SRT 和翻译 SRT：

```powershell
& '.\.venv\Scripts\python.exe' 'tools\subtitle_reviewer.py' `
  --media 'F:\path\sample.mp3' `
  --source-srt 'E:\path\source.srt' `
  --translated-srt 'E:\path\translated.srt'
```

浏览器会打开只读校对页。播放时当前原文和译文自动同步，点击字幕行可以直接跳到对应时间。

## 日常流程

1. 先生成报告，确认素材、参考文件和设置没有漂移。
2. 优先运行 `guardrail` 和 `anchor`，对照清单里的人工验收重点。
3. 记录人工结论；若预期与真实使用不一致，以人工实际审片为准并更新用例。
4. 短集稳定后再运行 `milestone`，测试长任务和断点续跑。

## 生成报告

```powershell
.\.venv\Scripts\python.exe tools\regression_workspace.py report
```

本地报告记录媒体身份、时长、画幅、编码、设置、参考文件、时间轴完整性和疑似说话人前缀。每个用例还应填写 `acceptance_checks` 和 `human_review`，但报告不会宣称翻译质量正确。

正式基线使用严格模式：

```powershell
.\.venv\Scripts\python.exe tools\regression_workspace.py report `
  --strict `
  --fail-on-regression `
  --output-dir test_workspace\reports\baseline-v1
```

- `passed`：已配置的客观检查通过。
- `failed`：出现未登记的结构回归，命令返回失败。
- `known_failure`：旧参考仍有已经登记、等待刷新的问题；报告保留问题，但不冒充新回归。
- `not_configured`：素材已进入队列，但还没有人工基准或自动阈值。

自动检查只负责文件存在性、字幕条数、重叠、倒退、双语行数、时间轴映射和明确异常文本。局部测试轴可通过 `source_time_offset_seconds` 映射回原视频时间；删除水印轴的案例可以声明允许缺少的时间轴数量。翻译自然度、听感和是否应补轴仍由人工验收。

`cases.example.json` 展示清单格式。本机实际使用由 Git 忽略的 `cases.local.json`。
