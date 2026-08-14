# UtaSyncNext 系统验证计划

目标：用分层测试确认 Next 可以独立运行、独立下载模型、独立调度 worker，并且不会误依赖 V3。

## 测试机器人

测试机器人分工为“验证员”：

- 先跑安全快测，确认环境、导入、核心脚本、翻译假 API、模型目录隔离。
- 再按需跑联网预检、模型加载、真实管线、真实下载。
- 默认不跑真实 API、不跑真实下载、不跑长视频 Demucs，避免消耗额度和时间。

统一入口：

```powershell
cd /d E:\traecode\UtaSync\UtaSyncNext
.venv\Scripts\python.exe tools\verify_next_stack.py --profile quick
```

## 测试分层

### 1. quick：默认安全快测

命令：

```powershell
.venv\Scripts\python.exe tools\verify_next_stack.py --profile quick
```

覆盖：

- Python 依赖存在性：`customtkinter`、`huggingface_hub`、`openai`、`httpx`
- Next 源码编译：`app/controllers/core/engine/ui/utils/tools`
- V3 动态包装扫描：禁止 `_load_v3`、`spec_from_file_location` 等旧包装
- 模型下载离线契约：Next 模型目录、代理归一化、repo 映射
- 翻译器假 API：双语、纯中文
- UI/worker/core 关键模块导入

不覆盖：

- 不联网
- 不加载 faster-whisper 模型
- 不跑 ASR 管线
- 不下载模型

### 2. network：联网清单预检，不下载模型

命令：

```powershell
.venv\Scripts\python.exe tools\verify_next_stack.py --profile network --model large-v2 --source official --proxy 10808
```

覆盖：

- HuggingFace 官方源/镜像源是否可访问
- 目标模型文件清单数量
- 目标模型总大小预估
- 代理 `10808` 是否可用

风险：

- 会联网，但不下载大文件。

### 3. model：本地模型加载测试

命令：

```powershell
.venv\Scripts\python.exe tools\verify_next_stack.py --profile model --model small
```

large-v2 下载完整后：

```powershell
.venv\Scripts\python.exe tools\verify_next_stack.py --profile model --model large-v2
```

覆盖：

- 本地 snapshot 完整性
- `faster-whisper` CPU int8 加载

风险：

- 首次加载大模型会慢。
- 如果 engine 依赖没装完整，会失败并提示缺包。

### 4. pipeline：真实 worker 管线烟测

小模型快速跑：

```powershell
.venv\Scripts\python.exe tools\verify_next_stack.py --profile pipeline --model small --ass --timeout 600
```

使用真实视频：

```powershell
.venv\Scripts\python.exe tools\verify_next_stack.py --profile pipeline --model large-v2 --media "F:\live人声分离实验\yama『End roll』Studio Live ver. - yama (1080p, h264).mp4" --ass --timeout 1200
```

开启 Demucs：

```powershell
.venv\Scripts\python.exe tools\verify_next_stack.py --profile pipeline --model small --media "F:\live人声分离实验\yama『End roll』Studio Live ver. - yama (1080p, h264).mp4" --demucs --ass --timeout 1800
```

覆盖：

- worker 子进程
- 音频提取
- Demucs 可选分离
- faster-whisper 打轴
- SRT 输出
- ASS 输出
- 缓存命中/重跑行为

风险：

- 会占用 GPU/CPU。
- Demucs 会很慢。
- large-v2 会明显更重。

### 5. download：真实模型下载

必须显式确认：

```powershell
.venv\Scripts\python.exe tools\verify_next_stack.py --profile download --model large-v2 --source official --proxy 10808 --confirm-download
```

覆盖：

- 真实下载
- 断点续传
- 下载后完整性校验

风险：

- 会下载数 GB 文件。
- 网络不稳时需要多次续传。

## 默认不自动跑的测试

以下测试必须人工确认后再跑：

- 真实大模型 API 翻译：会消耗 API 额度。
- large-v2 / large-v3 全量下载：文件大。
- large-v2 / large-v3 长视频管线：耗时长。
- Demucs 长视频分离：耗时长且占资源。
- 打包体积测试：需要额外安装 PyInstaller/Nuitka 等打包工具。
- 缓存清理/删除测试：涉及文件删除，必须单独确认。

## 推荐验证顺序

1. `quick`
2. `network --model large-v2 --proxy 10808`
3. `model --model small`
4. `pipeline --model small --ass`
5. large-v2 下载完整后跑 `model --model large-v2`
6. 用真实 yama 视频跑 `pipeline --model large-v2 --ass`
7. 最后再决定是否加入真实 API 翻译和打包测试

## Safe GPU Engine Upgrade

Do not run a long `pip install --force-reinstall torch ...` inside Codex while the desktop is busy.
Use the two-step wheelhouse flow instead:

```powershell
cd /d E:\traecode\UtaSync\UtaSyncNext
powershell -ExecutionPolicy Bypass -File tools\prepare_engine_gpu_wheels.ps1 -Proxy 10808
powershell -ExecutionPolicy Bypass -File tools\install_engine_gpu_wheels.ps1
.venv\Scripts\python.exe tools\check_engine_runtime.py
```

Expected runtime fingerprint after install:

- `torch==2.5.1+cu121`
- `torchaudio==2.5.1+cu121`
- `ctranslate2==4.7.1`
- `torch_cuda_available: true`

The prepare step downloads wheels only and does not modify `.engine`.
The install step uses local wheels only and modifies only `UtaSyncNext\.engine`.

If `tools\check_engine_runtime.py` reports `asr_cuda_runtime_ready: false`, the
pipeline is still usable: Faster-Whisper will skip CUDA and use CPU int8. This
is slower but avoids crashing when CUDA DLLs such as `cublas64_12.dll` are not
available.

## Fast Health Check

Use this after small UI/engine changes. It does not download models, import Torch, load Whisper,
run translation smoke tests, import Tk/UI modules, or execute the pipeline.

```powershell
cd /d E:\traecode\UtaSync\UtaSyncNext
.venv\Scripts\python.exe tools\verify_next_stack.py --profile health --json-report test_logs\health.json
```

Expected coverage:

- Next-local HuggingFace cache environment:
  `UtaSyncNext\.cache\huggingface`
- Python package presence for the light UI environment.
- Next source compile check.
- Legacy wrapper scan, excluding heavy/cache directories.
- Audio cache policy regression.
- Offline model registry scan.

Run `--profile quick` when you also want translator smoke tests and UI import checks.

## Lightweight Launcher Packaging

The launcher package must stay thin. Build dependencies are separate from runtime UI
dependencies:

```powershell
cd /d E:\traecode\UtaSync\UtaSyncNext
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

Run the boundary check before any packaging attempt:

```powershell
.venv\Scripts\python.exe tools\check_launcher_boundary.py
```

Build the launcher:

```powershell
powershell -ExecutionPolicy Bypass -File tools\package_launcher.ps1 -Clean
```

Audit an existing build without rebuilding:

```powershell
.venv\Scripts\python.exe tools\check_packaging_size.py
```

Expected packaging boundary:

- Launcher does not include `.engine`, `models_faster`, `.cache`, `.wheelhouse`, or `output`.
- Launcher startup must not import `torch`, `faster_whisper`, `demucs`, `whisper`, or `ctranslate2`.
- Heavy engine packages remain external under `UtaSyncNext\.engine`.
- Models remain external under `UtaSyncNext\models_faster`.

## Demucs Compatibility Regression

Demucs weights are stored under the Next-local cache:

```powershell
UtaSyncNext\.cache\torch\hub\checkpoints
```

The current engine uses `torchaudio==2.11.0`, whose WAV save path can require
`torchcodec`. UtaSyncNext routes Demucs through `engine\demucs_runner.py` so
Demucs can save `vocals.wav` with the Python standard `wave` module instead of
requiring another heavy package.

Verified commands:

```powershell
.venv\Scripts\python.exe tools\smoke_full_pipeline.py --model small --media "E:\traecode\UtaSync\UtaSyncNext\smoke_media\yama_endroll_45s.wav" --demucs --ass --timeout 1800
.venv\Scripts\python.exe tools\smoke_full_pipeline.py --model small --media "E:\traecode\UtaSync\UtaSyncNext\smoke_media\yama_endroll_45s.wav" --demucs --ass --timeout 600
```

Expected result:

- First run completes Demucs + ASR + ASS.
- Second run should hit the pipeline cache and complete quickly.
- Launcher boundary check must still pass; `demucs_runner.py` must not add a
  startup import of `torch`, `torchaudio`, `demucs`, `faster_whisper`, or
  `ctranslate2`.

## Cache And Default Scheduling Policy

Next favors fast first results by default:

- New configs default to `先跳过 Demucs (极速/推荐先出轴)`.
- Demucs remains available as `开启 Demucs 人声分离 (高质量/很慢)`.
- Speech mode always skips Demucs.

Pipeline caches must be fast but not stale. The ASR cache fingerprint includes:

- media path, size, modified time, and edge content hash
- normalized audio mode
- selected Whisper model and snapshot path
- Demucs skip/enabled state
- hardware/Demucs scheduling mode

The translation cache fingerprint includes:

- media fingerprint
- source SRT content hash
- reference lyrics
- output mode
- API URL and model id

Run this after cache/scheduling changes:

```powershell
.venv\Scripts\python.exe tools\verify_next_stack.py --profile health
.venv\Scripts\python.exe tools\smoke_full_pipeline.py --model small --media "E:\traecode\UtaSync\UtaSyncNext\smoke_media\yama_endroll_45s.wav" --ass --timeout 120
```

Expected result:

- Health includes `demucs-setting-policy` and `pipeline-cache-fingerprint`.
- First run after a fingerprint version change may rebuild ASR once.
- Second run should report `命中打轴缓存`.

## Private Config And Real API Smoke

Private API configuration lives in `user_settings.json`. This file must stay
local-only and ignored by Git. Commit-safe defaults live in
`user_settings.example.json`.

Before pushing or packaging a public source bundle, run:

```powershell
.venv\Scripts\python.exe tools\check_no_secrets.py
git check-ignore -v user_settings.json .env user_presets.json
```

Real API smoke tests are opt-in only:

```powershell
.venv\Scripts\python.exe tools\smoke_real_translation.py --srt "output\yama_endroll_45s\subtitles\yama_endroll_45s_极致打轴.srt" --mode bilingual --in-place
.venv\Scripts\python.exe tools\smoke_full_pipeline.py --model small --media "E:\traecode\UtaSync\UtaSyncNext\smoke_media\yama_endroll_45s.wav" --ass --use-settings-api --translation-mode chinese_only --timeout 600
```

Expected result:

- API key is reported only as present/length, never printed.
- Real translation smoke fails if the output text is identical to the input.
- Full pipeline smoke can use cached ASR/Demucs work and still verify real API
  translation plus ASS generation.
