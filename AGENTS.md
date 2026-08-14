# UtaSyncNext Working Boundary

This repository is the only writable scope for UtaSyncNext work.

## V3 Is Read-Only

- The parent directory is the UtaSync v3 repository and is reference material only.
- Reading v3 source with `rg`, `Get-Content`, `git show`, or `git diff` is allowed.
- Never create, edit, delete, move, format, stage, commit, or restore files in the parent repository.
- Never run a formatter, code generator, package command, or test command with the parent repository as its working directory.
- Do not copy Next changes back into v3. Reuse ideas by implementing them inside this repository.
- If a requested task genuinely requires changing v3, stop and ask the user for separate explicit approval.

## Required Safety Check

Before editing Next:

```powershell
.\.venv\Scripts\python.exe tools\v3_guard.py begin
```

After editing and testing Next:

```powershell
.\.venv\Scripts\python.exe tools\v3_guard.py verify
```

If verification reports a v3 change, stop immediately. Report the paths and do not revert them automatically.

## Git Scope

- Commit only files owned by this repository.
- Keep `.engine`, `.venv`, models, output, logs, media, private settings, API keys, and reference lyrics out of Git.
- Run `tools/check_no_secrets.py` and `tools/v3_guard.py verify` before every commit.
- Use the parent v3 Git history only for read-only comparison.
