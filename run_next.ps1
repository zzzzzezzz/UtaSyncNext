$ErrorActionPreference = "Stop"

$nextRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $nextRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    Write-Host "UtaSyncNext .venv was not found."
    Write-Host "Please run:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File tools\bootstrap_venv.ps1"
    Read-Host "Press Enter to exit"
    exit 1
}

Set-Location $nextRoot
$env:HF_HOME = Join-Path $nextRoot ".cache\huggingface"
$env:HUGGINGFACE_HUB_CACHE = Join-Path $nextRoot ".cache\huggingface\hub"
$env:HF_HUB_CACHE = Join-Path $nextRoot ".cache\huggingface\hub"
$env:TRANSFORMERS_CACHE = Join-Path $nextRoot ".cache\huggingface\transformers"
$env:XDG_CACHE_HOME = Join-Path $nextRoot ".cache"
$env:HF_HUB_DISABLE_XET = "1"
& $pythonExe app.py
