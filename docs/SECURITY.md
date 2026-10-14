# 密钥与隐私边界

这份文档回答一个问题：把 UtaSyncNext 推到远端仓库之后，密钥和个人信息会不会漏出去。

结论先说：截至 2026-10-14，Next 仓库没有任何真实密钥，整个 Git 历史也没有提交过密钥、证书或私密设置文件；已经公开的 V3 仓库同样干净。下面是可复核的证据、自动闸门，以及公开前需要你拍板的选择。

## 密钥只存在于一个本地文件

- 唯一存放点是工作区根目录的 `user_settings.json`，`api_key` 和 `api_key_design` 以明文写在里面。
- 代码不会从环境变量、`.env`、注册表或系统凭据管理器补填密钥；UI 里不填就一直为空（见 [`CONFIGURATION.md`](CONFIGURATION.md)）。
- 翻译预设只写 Base URL 和模型名，不含密钥。
- `user_settings.json` 由 `.gitignore` 拦截，永远不应该进入 Git。

## 会被 Git 忽略的清单

来自 [`.gitignore`](../.gitignore)：

| 类别 | 模式 |
| --- | --- |
| 本地设置与预设 | `user_settings.json`、`user_presets.json`、`user_font_pairs.json`、`*.local.json`、`secrets.*`、`.env`、`.env.*` |
| 运行资产 | `.venv/`、`.engine/`、`.cache/`、`.wheelhouse/`、`models_faster/`、`fonts/`、`output/`、`performance/` |
| 媒体与字幕产物 | `smoke_media/`、`input/`、`*.mp4`、`*.mkv`、`*.wav`、`*.srt`、`*.ass`、`*.hash` 等 |
| 日志 | `*.log`、`crash_trace.log`、`launcher_crash.log` |
| 私有歌词 | `test_assets/lyrics_refs/*.txt`，只保留 `README.md` |
| 构建产物 | `build/`、`dist/`、`__pycache__/` |

真正进 Git 的只有一个模板文件 `user_settings.example.json`。

## 已经做过的扫描（可复现）

| 检查 | 范围 | 结果 |
| --- | --- | --- |
| 硬密钥模式 | 本次审计时的全部已跟踪文件 | 0 命中（`sk-`、`ghp_`、`github_pat_`、`hf_`、`AIza`、私钥头、`xox*`、`glpat-`） |
| 硬密钥模式 | 本次审计时的全部提交和 Git 对象 | 0 命中 |
| `api_key` 实际取值 | 全部历史 | 只出现过 `""` 和 `"fake-key"`，没有真实值 |
| 敏感文件名 | 全部历史 | 从未提交 `user_settings.json`、`user_presets.json`、`.env`、`*.pem`、`*.key`、`id_rsa` |
| 本机用户名 | 已跟踪文件 | 无 `C:\Users\<用户名>`；只有 `tools/verify_next_stack.py` 里一条用于断言缓存隔离的比较字符串 |
| 仓库体积 | 全历史 | 统计随提交变化；发布前用 `git count-objects -v` 和大文件检查重新确认 |
| V3 公开仓库 | 11 个已跟踪文件 + 全部历史 | 只有 `YOUR_API_KEY` 占位符，无真实密钥；未提交 `user_settings.json` |

在自己机器上复现：

```powershell
.\.venv\Scripts\python.exe tools\check_no_secrets.py
git grep -n -I -E "sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_|hf_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----" $(git rev-list --all) -- .
```

## 提交前的自动闸门

1. `.githooks/pre-commit` 在每次提交前跑 `tools/check_no_secrets.py` 和 `tools/v3_guard.py verify`。克隆之后要 `git config core.hooksPath .githooks` 才会生效，这一步写在 README 的克隆说明里。
2. `.github/workflows/verify.yml` 在 CI 里也会跑 `check_no_secrets.py`，防止有人绕过本地钩子。
3. `tools/check_no_secrets.py` 会拦下 12 字符以上、非占位符的 `api_key` 字面量。

## 留在 Git 里、但不属于密钥的东西

`test_workspace/` 下 35 个已跟踪文件是回归证据，里面记录了生成证据时的本机绝对路径，例如 `E:\traecode\UtaSync\...` 和 `F:\live人声分离实验\ikura.mp4`。

这不是密钥，但会暴露开发机的目录结构和一个个人文件夹名。如果你打算公开仓库，可以把这些字段统一替换成占位路径，数值结论不会受影响。默认不删改，等你决定。

## 公开前需要你拍板的三件事

1. **提交邮箱**：当前所有提交的作者和提交者都是 `zzzzzezzz <869768200@qq.com>`，公开后任何人都能看到。不想暴露的话，可以改成 noreply 地址再推；V3 的历史里已经用过 `53385348+zzzzzezzz@users.noreply.github.com`，可以直接沿用。
2. **许可证**：仓库目前没有 `LICENSE`。想开放就给 MIT 或 Apache-2.0，只想私有备份就不加。
3. **远端仓库位置**：GitHub、Gitee 还是自建 GitLab。Next 目前没有配置任何 remote。

## 万一密钥已经泄漏

1. 立刻到对应平台吊销并轮换该 Key，这是唯一真正有效的止血手段。
2. 用 `git filter-repo` 或 BFG 从历史里清除该字符串，然后强推。
3. 通知任何可能已经拿到该 Key 的服务或人。
4. 当前仓库还没有推过，所以现在做任何清理都不需要重写已发布历史。
