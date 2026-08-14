param(
    [switch]$Engine,
    [switch]$EngineOnly,
    [string]$Proxy = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$nextRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$uiVenvPath = Join-Path $nextRoot ".venv"
$uiPythonPath = Join-Path $uiVenvPath "Scripts\python.exe"
$engineVenvPath = Join-Path $nextRoot ".engine"
$enginePythonPath = Join-Path $engineVenvPath "Scripts\python.exe"

function Invoke-PipInstall {
    param(
        [string]$PythonPath,
        [string[]]$PipArgs
    )

    $args = @("-m", "pip") + $PipArgs
    if ($Proxy.Trim()) {
        $normalizedProxy = $Proxy.Trim()
        if ($normalizedProxy -match '^\d{1,5}$') {
            $normalizedProxy = "http://127.0.0.1:$normalizedProxy"
        } elseif ($normalizedProxy -notmatch '^[a-zA-Z][a-zA-Z0-9+.-]*://') {
            $normalizedProxy = "http://$normalizedProxy"
        }
        $args += @("--proxy", $normalizedProxy)
    }

    & $PythonPath @args
}

function Ensure-Venv {
    param(
        [string]$VenvPath,
        [string]$PythonPath,
        [string]$Label
    )

    if (-not (Test-Path $PythonPath)) {
        Write-Host "Creating $Label virtual environment at $VenvPath"
        & $Python -m venv $VenvPath
    }

    Write-Host "Upgrading pip for $Label..."
    Invoke-PipInstall -PythonPath $PythonPath -PipArgs @("install", "--upgrade", "pip")
}

if (-not $EngineOnly) {
    Ensure-Venv -VenvPath $uiVenvPath -PythonPath $uiPythonPath -Label "UtaSyncNext UI"

    Write-Host "Installing lightweight UI/download dependencies..."
    Invoke-PipInstall -PythonPath $uiPythonPath -PipArgs @("install", "-r", (Join-Path $nextRoot "requirements-ui.txt"))
} else {
    Write-Host "EngineOnly enabled; skipped UI .venv bootstrap."
}

if ($Engine) {
    Ensure-Venv -VenvPath $engineVenvPath -PythonPath $enginePythonPath -Label "UtaSyncNext engine"

    Write-Host "Installing heavy engine dependencies into .engine..."
    Write-Host "This may take a long time and download several GB."
    Invoke-PipInstall -PythonPath $enginePythonPath -PipArgs @("install", "-r", (Join-Path $nextRoot "requirements-engine.txt"))
} else {
    Write-Host "Skipped heavy engine dependencies. Re-run with -Engine when ready."
}

Write-Host ""
Write-Host "Done."
Write-Host "UI environment:"
Write-Host "  $uiVenvPath\Scripts\Activate.ps1"
Write-Host "Engine environment:"
Write-Host "  $engineVenvPath\Scripts\Activate.ps1"
