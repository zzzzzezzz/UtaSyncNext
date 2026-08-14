param(
    [string]$Wheelhouse = "",
    [ValidateSet("CTranslate2", "TorchCuda", "All")]
    [string]$Scope = "All"
)

$ErrorActionPreference = "Stop"

try {
    [System.Diagnostics.Process]::GetCurrentProcess().PriorityClass = "BelowNormal"
} catch {
    Write-Host "Could not lower process priority: $($_.Exception.Message)"
}

$nextRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$enginePython = Join-Path $nextRoot ".engine\Scripts\python.exe"
if (-not $Wheelhouse.Trim()) {
    $Wheelhouse = Join-Path $nextRoot ".wheelhouse\engine-gpu-cu121"
}
$logDir = Join-Path $nextRoot "test_logs"
$logPath = Join-Path $logDir "install_engine_gpu_wheels.log"

if (-not (Test-Path $enginePython)) {
    throw "Next .engine was not found. Run tools\bootstrap_venv.ps1 -EngineOnly -Engine first."
}
if (-not (Test-Path $Wheelhouse)) {
    throw "Wheelhouse not found: $Wheelhouse. Run tools\prepare_engine_gpu_wheels.ps1 first."
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$pipArgs = @(
    "-m", "pip", "install",
    "--no-index",
    "--find-links", $Wheelhouse,
    "--no-deps",
    "--upgrade"
)

if ($Scope -eq "CTranslate2") {
    $pipArgs += @("ctranslate2==4.7.1")
} elseif ($Scope -eq "TorchCuda") {
    $pipArgs += @("torch==2.5.1+cu121", "torchaudio==2.5.1+cu121")
} else {
    $pipArgs += @("ctranslate2==4.7.1", "torch==2.5.1+cu121", "torchaudio==2.5.1+cu121")
}

Write-Host "Installing GPU core wheels from: $Wheelhouse"
Write-Host "Scope: $Scope"
Write-Host "Log: $logPath"
Write-Host "This step modifies only UtaSyncNext\.engine."

& $enginePython @pipArgs *>&1 | Tee-Object -FilePath $logPath
if ($LASTEXITCODE -ne 0) {
    throw "pip install failed with exit code $LASTEXITCODE. See $logPath"
}

Write-Host ""
Write-Host "GPU core install complete. Verify with:"
Write-Host "  .venv\Scripts\python.exe tools\check_engine_runtime.py"
