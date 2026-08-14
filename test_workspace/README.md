# UtaSyncNext Regression Workspace

This directory keeps regression-test definitions separate from application code and output.

## Boundaries

- Source media, v3 output, and approved subtitles are read-only references.
- Large media files are referenced by path and are never copied here.
- `cases.local.json`, reports, and run artifacts stay local and are ignored by Git.
- A v3 automatic output is historical evidence, not a human-approved answer.

## Reference Levels

- `v3_auto`: untouched automatic output from v3.
- `next_auto`: untouched automatic output from Next.
- `human_approved`: a subtitle reviewed and accepted by the user.

Only `human_approved` can be treated as a quality baseline. Automatic references are useful for detecting structural and behavioral changes.

## Generate A Report

```powershell
.\.venv\Scripts\python.exe tools\regression_workspace.py report
```

The local report records media identity, selected settings, reference files, timeline integrity, long lines, and suspected speaker-label prefixes. It does not claim that translation quality is correct.

`cases.example.json` documents the manifest format. The working machine uses the ignored `cases.local.json` file.
