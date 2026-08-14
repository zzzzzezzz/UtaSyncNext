param(
    [switch]$Force,
    [switch]$SkipEngine,
    [switch]$SkipModels,
    [switch]$LinkCache
)

$ErrorActionPreference = "Stop"

$nextRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$distRoot = Join-Path $nextRoot "dist\UtaSyncNext"

function Assert-ChildPath {
    param(
        [string]$PathToCheck,
        [string]$ExpectedRoot
    )

    $fullPath = [System.IO.Path]::GetFullPath($PathToCheck)
    $fullRoot = [System.IO.Path]::GetFullPath($ExpectedRoot)
    if (-not $fullPath.StartsWith($fullRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside dist runtime: $fullPath"
    }
}

function Link-RuntimeDir {
    param(
        [string]$Name,
        [string]$Source
    )

    if (-not (Test-Path $Source)) {
        Write-Warning "Skip $Name because source does not exist: $Source"
        return
    }

    $target = Join-Path $distRoot $Name
    Assert-ChildPath -PathToCheck $target -ExpectedRoot $distRoot

    if (Test-Path $target) {
        if (-not $Force) {
            Write-Host "Skip $Name because destination already exists: $target"
            Write-Host "Pass -Force to replace it."
            return
        }
        $existing = Get-Item -LiteralPath $target -Force
        $isLink = ($existing.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        if ($isLink) {
            [System.IO.Directory]::Delete($target, $false)
        }
        else {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
    }

    New-Item -ItemType Junction -Path $target -Target $Source | Out-Null
    Write-Host "Linked $Name -> $Source"
}

if (-not (Test-Path (Join-Path $distRoot "UtaSyncNext.exe"))) {
    throw "Packaged launcher was not found. Build it first with tools\package_launcher.ps1 -Clean"
}

if (-not $SkipEngine) {
    Link-RuntimeDir -Name ".engine" -Source (Join-Path $nextRoot ".engine")
}

if (-not $SkipModels) {
    Link-RuntimeDir -Name "models_faster" -Source (Join-Path $nextRoot "models_faster")
}

if ($LinkCache) {
    Link-RuntimeDir -Name ".cache" -Source (Join-Path $nextRoot ".cache")
}

Write-Host ""
Write-Host "Dist runtime is ready for local smoke testing:"
Write-Host "  $distRoot"
