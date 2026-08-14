param(
    [string]$Proxy = "",
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
$requirements = Join-Path $nextRoot "requirements-engine-gpu-core.txt"
if (-not $Wheelhouse.Trim()) {
    $Wheelhouse = Join-Path $nextRoot ".wheelhouse\engine-gpu-cu121"
}
$logDir = Join-Path $nextRoot "test_logs"
$logPath = Join-Path $logDir "prepare_engine_gpu_wheels.log"

if (-not (Test-Path $enginePython)) {
    throw "Next .engine was not found. Run tools\bootstrap_venv.ps1 -EngineOnly -Engine first."
}

New-Item -ItemType Directory -Force -Path $Wheelhouse | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$pipArgs = @(
    "-m", "pip", "download",
    "--only-binary=:all:",
    "--no-deps",
    "--dest", $Wheelhouse
)

if ($Scope -eq "CTranslate2") {
    $pipArgs += @("ctranslate2==4.7.1")
} elseif ($Scope -eq "TorchCuda") {
    $pipArgs += @("--extra-index-url", "https://download.pytorch.org/whl/cu121", "torch==2.5.1+cu121", "torchaudio==2.5.1+cu121")
} else {
    $pipArgs += @("-r", $requirements)
}

if ($Proxy.Trim()) {
    $normalizedProxy = $Proxy.Trim()
    if ($normalizedProxy -match '^\d{1,5}$') {
        $normalizedProxy = "http://127.0.0.1:$normalizedProxy"
    } elseif ($normalizedProxy -notmatch '^[a-zA-Z][a-zA-Z0-9+.-]*://') {
        $normalizedProxy = "http://$normalizedProxy"
    }
    $pipArgs += @("--proxy", $normalizedProxy)
}

Write-Host "Downloading GPU core wheels to: $Wheelhouse"
Write-Host "Scope: $Scope"
Write-Host "Log: $logPath"
Write-Host "This step does not modify .engine."

& $enginePython @pipArgs *>&1 | Tee-Object -FilePath $logPath
if ($LASTEXITCODE -ne 0) {
    throw "pip download failed with exit code $LASTEXITCODE. See $logPath"
}

Write-Host ""
Write-Host "Wheel download complete. Install later with:"
Write-Host "  powershell -ExecutionPolicy Bypass -File tools\install_engine_gpu_wheels.ps1 -Scope $Scope"
