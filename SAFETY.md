# UtaSyncNext 安全工作流

## 目标

Next 可以继续读取父目录中的 UtaSync v3 源码作为参考，但任何 Next 开发任务都不能修改、格式化、提交或覆盖 v3。

目录关系：

```text
E:\traecode\UtaSync\              UtaSync v3，只读参考
E:\traecode\UtaSync\UtaSyncNext\ UtaSyncNext，唯一可写仓库
```

## 为什么采用独立仓库

分支和口头规则只能减少误提交，无法阻止文件先被修改。将 `UtaSyncNext` 作为独立 Git 仓库，并在 Codex 中只打开这个目录后，父目录 v3 会处于工作区之外。Next 仍然可以读取 v3，但写入会被工作区权限拦截。

## 每次开发的自动流程

项目级 `AGENTS.md` 要求开发代理执行以下流程，用户不需要逐文件人工检查。

开始任务：

```powershell
.\.venv\Scripts\python.exe tools\v3_guard.py begin
```

完成任务：

```powershell
.\.venv\Scripts\python.exe tools\v3_guard.py verify
```

`begin` 会记录当前 v3 的内容指纹，包括：

- v3 Git 已跟踪文件；
- v3 根目录的源码和配置文件；
- `core`、`controllers`、`ui`、`utils`、`packaging` 中尚未提交的源码。

`verify` 只比较变化。它不会修改或还原 v3；如果用户同时修改了 v3，也只会报警并停止当前 Next 提交。

## Git 提交保护

仓库使用 `.githooks/pre-commit`：

1. 检查待提交源码是否含明显 API Key；
2. 校验 v3 指纹没有变化；
3. 任一检查失败就拒绝提交。

克隆仓库后执行一次：

```powershell
git config core.hooksPath .githooks
```

## 如何参考 v3

允许的只读操作示例：

```powershell
rg "关键词" ..\core ..\ui
git -C .. show HEAD:core/generator.py
git -C .. diff -- core/generator.py
```

禁止对 `..` 使用 `apply_patch`、格式化器、文件写入命令、`git add`、`git commit`、`git restore` 或清理命令。

## Codex 工作区

后续新会话应直接选择：

```text
E:\traecode\UtaSync\UtaSyncNext
```

不要再将 `E:\traecode\UtaSync` 作为 Next 开发会话的可写工作区。这样既能查看父目录 v3，又能让工作区权限阻止误写。
