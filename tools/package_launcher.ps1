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

function Remove-DistRuntimeLink {
    param([string]$Name)

    $target = Join-Path (Join-Path $distRoot "UtaSyncNext") $Name
    if (-not (Test-Path $target)) {
        return
    }

    $item = Get-Item -LiteralPath $target -Force
    $isLink = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    if ($isLink) {
        [System.IO.Directory]::Delete($target, $false)
        Write-Host "Removed runtime junction before rebuild: dist\UtaSyncNext\$Name"
        return
    }

    if ($Name -eq ".cache") {
        Remove-Item -LiteralPath $target -Recurse -Force
        Write-Host "Removed packaged runtime cache before rebuild: dist\UtaSyncNext\$Name"
        return
    }

    throw "dist\UtaSyncNext\$Name is a real directory, not a junction. Move it before rebuilding the launcher."
}

function Prepare-DistForRebuild {
    foreach ($name in @(".engine", "models_faster", ".cache")) {
        Remove-DistRuntimeLink -Name $name
    }
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
    Copy-Item -LiteralPath (Join-Path $nextRoot "app.py") -Destination (Join-Path $isolatedRoot "app.py") -Force
    Copy-Item -LiteralPath (Join-Path $nextRoot "packaging\launcher.spec") -Destination $specPath -Force

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

$args = @($specPath, "--noconfirm")
if ($Clean) {
    $args += "--clean"
}

Write-Host "Building lightweight UtaSyncNext Launcher..."
Set-NextBuildCacheEnv
Prepare-DistForRebuild
Reset-IsolatedLauncherSource
Write-Host "Spec: $specPath"
try {
    Set-Location $isolatedRoot
    & $pythonExe -m PyInstaller @args "--distpath" $distRoot "--workpath" (Join-Path $buildRoot "launcher")
}
finally {
    Set-Location $nextRoot
}

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Launcher build complete:"
Write-Host "  dist\UtaSyncNext\UtaSyncNext.exe"
Write-Host ""
Write-Host "Remember: .engine, models_faster, output, and .cache are external runtime folders."

& $pythonExe tools\check_packaging_size.py
if ($LASTEXITCODE -ne 0) {
    throw "Packaging audit failed. The launcher dist may include heavy AI payloads."
}
