param(
    [switch]$Clean,
    [switch]$AuditOnly
)

$ErrorActionPreference = "Stop"

$nextRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $nextRoot ".venv\Scripts\python.exe"
$buildRoot = Join-Path $nextRoot "build"
$distRoot = Join-Path $nextRoot "dist"
$isolatedRoot = Join-Path $buildRoot "launcher_source"
$specPath = Join-Path $isolatedRoot "packaging\launcher.spec"
$cacheRoot = Join-Path $nextRoot ".cache"
$runtimeBackupRoot = Join-Path $buildRoot "dist_runtime_backup"
$runtimeStateNames = @(
    ".engine",
    "models_faster",
    "output",
    ".cache",
    "performance",
    "fonts",
    "user_settings.json"
    "user_presets.json",
    "user_font_pairs.json"
)

function Set-NextBuildCacheEnv {
    $hfHome = Join-Path $cacheRoot "huggingface"
    New-Item -ItemType Directory -Path $hfHome -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $hfHome "hub") -Force | Out-Null

    $env:HF_HOME = $hfHome
    $env:HUGGINGFACE_HUB_CACHE = Join-Path $hfHome "hub"
    $env:HF_HUB_CACHE = $env:HUGGINGFACE_HUB_CACHE
    $env:TRANSFORMERS_CACHE = Join-Path $cacheRoot "transformers"
    $env:XDG_CACHE_HOME = $cacheRoot
    $env:HF_HUB_DISABLE_XET = "1"
}

function Backup-DistRuntimeState {
    if (Test-Path -LiteralPath $runtimeBackupRoot) {
        Remove-Item -LiteralPath $runtimeBackupRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $runtimeBackupRoot -Force | Out-Null

    $distAppRoot = Join-Path $distRoot "UtaSyncNext"
    foreach ($name in $runtimeStateNames) {
        $source = Join-Path $distAppRoot $name
        if (-not (Test-Path -LiteralPath $source)) {
            continue
        }
        Move-Item -LiteralPath $source -Destination (Join-Path $runtimeBackupRoot $name) -Force
        Write-Host "Backed up packaged runtime state: dist\UtaSyncNext\$name"
    }
}

function Restore-DistRuntimeState {
    if (-not (Test-Path -LiteralPath $runtimeBackupRoot)) {
        return
    }

    $distAppRoot = Join-Path $distRoot "UtaSyncNext"
    $blocked = @()
    foreach ($name in $runtimeStateNames) {
        $backup = Join-Path $runtimeBackupRoot $name
        if (-not (Test-Path -LiteralPath $backup)) {
            continue
        }
        $target = Join-Path $distAppRoot $name
        if (Test-Path -LiteralPath $target) {
            $blocked += $name
            continue
        }
        Move-Item -LiteralPath $backup -Destination $target -Force
        Write-Host "Restored packaged runtime state: dist\UtaSyncNext\$name"
    }

    if ($blocked.Count -gt 0) {
        throw "Cannot restore packaged runtime state: unexpected build paths $($blocked -join ', '). Backups kept in $runtimeBackupRoot"
    }

    Remove-Item -LiteralPath $runtimeBackupRoot -Recurse -Force
}

function Reset-IsolatedLauncherSource {
    $buildFull = [System.IO.Path]::GetFullPath($buildRoot)
    $targetFull = [System.IO.Path]::GetFullPath($isolatedRoot)
    if (-not $targetFull.StartsWith($buildFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to reset unexpected build source path: $targetFull"
    }

    if (Test-Path $isolatedRoot) {
        Remove-Item -LiteralPath $isolatedRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $isolatedRoot | Out-Null

    foreach ($dirName in @("controllers", "core", "engine", "ui", "utils")) {
        Copy-Item -LiteralPath (Join-Path $nextRoot $dirName) -Destination (Join-Path $isolatedRoot $dirName) -Recurse -Force
    }

    New-Item -ItemType Directory -Path (Join-Path $isolatedRoot "packaging") | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $isolatedRoot "assets") | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $isolatedRoot "tools") | Out-Null
    Copy-Item -LiteralPath (Join-Path $nextRoot "app.py") -Destination (Join-Path $isolatedRoot "app.py") -Force
    Copy-Item -LiteralPath (Join-Path $nextRoot "packaging\launcher.spec") -Destination $specPath -Force
    Copy-Item -LiteralPath (Join-Path $nextRoot "packaging\assets\utasync.png") -Destination (Join-Path $isolatedRoot "assets\utasync.png") -Force
    Copy-Item -LiteralPath (Join-Path $nextRoot "packaging\assets\utasync.ico") -Destination (Join-Path $isolatedRoot "assets\utasync.ico") -Force
    Copy-Item -LiteralPath (Join-Path $nextRoot "tools\__init__.py") -Destination (Join-Path $isolatedRoot "tools\__init__.py") -Force
    Copy-Item -LiteralPath (Join-Path $nextRoot "tools\subtitle_reviewer.py") -Destination (Join-Path $isolatedRoot "tools\subtitle_reviewer.py") -Force

    Get-ChildItem -LiteralPath $isolatedRoot -Recurse -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force
}

if (-not (Test-Path $pythonExe)) {
    throw "UtaSyncNext .venv was not found. Run tools\bootstrap_venv.ps1 first."
}

Set-Location $nextRoot

& $pythonExe tools\check_launcher_boundary.py
if ($LASTEXITCODE -ne 0) {
    throw "Launcher boundary check failed."
}

if ($AuditOnly) {
    & $pythonExe tools\check_packaging_size.py
    exit $LASTEXITCODE
}

$pyInstallerCheck = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
& $pythonExe -c $pyInstallerCheck
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller is not installed in .venv."
    Write-Host "Install build-only dependencies when you are ready:"
    Write-Host "  .venv\Scripts\python.exe -m pip install -r requirements-build.txt"
    exit 2
}

$pyiArgs = @($specPath, "--noconfirm")
if ($Clean) {
    $pyiArgs += "--clean"
}

Write-Host "Building lightweight UtaSyncNext Launcher..."
Set-NextBuildCacheEnv
Reset-IsolatedLauncherSource
Write-Host "Spec: $specPath"
$buildExitCode = 1
try {
    Backup-DistRuntimeState
    Set-Location $isolatedRoot
    & $pythonExe -m PyInstaller @pyiArgs "--distpath" $distRoot "--workpath" (Join-Path $buildRoot "launcher")
    $buildExitCode = $LASTEXITCODE
}
finally {
    Set-Location $nextRoot
    Restore-DistRuntimeState
}

if ($buildExitCode -ne 0) {
    throw "PyInstaller build failed with exit code $buildExitCode."
}

Write-Host ""
Write-Host "Launcher build complete:"
Write-Host "  dist\UtaSyncNext\UtaSyncNext.exe"
Write-Host ""
Write-Host "Remember: .engine, models_faster, output, .cache, performance, and fonts are external runtime folders, and user_settings.json / user_presets.json / user_font_pairs.json keep the user state across rebuilds."

& $pythonExe tools\check_packaging_size.py
if ($LASTEXITCODE -ne 0) {
    throw "Packaging audit failed. The launcher dist may include heavy AI payloads."
}
