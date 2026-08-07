#Requires -Version 5.1
<#
.SYNOPSIS
  Build the Macro Review portable Windows ZIP (PyInstaller one-folder).

.PARAMETER Channel
  Release channel written into build_info.json (preview|stable|dev).

.PARAMETER Version
  Semantic version override (default: version_info.APP_VERSION).

.PARAMETER Commit
  Git commit SHA (default: current HEAD).

.PARAMETER SkipZip
  Build the folder only; do not create the ZIP.
#>
param(
    [ValidateSet("preview", "stable", "dev")]
    [string]$Channel = "preview",
    [string]$Version = "",
    [string]$Commit = "",
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Get-AppVersion {
    if ($Version) { return $Version }
    $match = Select-String -Path (Join-Path $Root "version_info.py") -Pattern 'APP_VERSION\s*=\s*"([^"]+)"'
    if (-not $match) { throw "Could not read APP_VERSION from version_info.py" }
    return $match.Matches[0].Groups[1].Value
}

function Get-GitCommit {
    if ($Commit) { return $Commit }
    try {
        return (git rev-parse HEAD).Trim()
    } catch {
        return "unknown"
    }
}

$AppVersion = Get-AppVersion
$GitCommit = Get-GitCommit
$Short = if ($GitCommit.Length -ge 7) { $GitCommit.Substring(0, 7) } else { $GitCommit }
$Stamp = Get-Date -Format "yyyyMMdd"

Write-Host "Building Macro Review $AppVersion ($Channel) commit=$Short"

$Dist = Join-Path $Root "dist"
$Build = Join-Path $Root "build"
$Stage = Join-Path $Dist "MacroReview"
$OutName = "MacroReview-$AppVersion-$Channel-$Short-win64"
if ($Channel -eq "stable") {
    $OutName = "MacroReview-$AppVersion-win64"
}
$ZipPath = Join-Path $Dist "$OutName.zip"
$ChecksumPath = Join-Path $Dist "$OutName.zip.sha256"
$ManifestPath = Join-Path $Dist "$OutName.manifest.json"

if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
if (Test-Path (Join-Path $Build "MacroReview")) { Remove-Item -Recurse -Force (Join-Path $Build "MacroReview") }

$BuildInfo = Join-Path $Root "build_info.json"
$BuildInfoObj = [ordered]@{
    version  = $AppVersion
    channel  = $Channel
    commit   = $GitCommit
    built_at = (Get-Date).ToUniversalTime().ToString("o")
    repo     = "mythorath/macro-review"
}
$json = $BuildInfoObj | ConvertTo-Json
[System.IO.File]::WriteAllText($BuildInfo, $json)

python -m pip install -r (Join-Path $Root "requirements-gui.txt")
python -m pip install -r (Join-Path $Root "packaging\requirements-build.txt")

python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $Dist `
    --workpath $Build `
    (Join-Path $Root "packaging\macroreview.spec")

if (-not (Test-Path (Join-Path $Stage "MacroReview.exe"))) {
    throw "Build failed: MacroReview.exe missing at $Stage"
}

# Copy pipeline payload beside the executable.
$PipelineDir = Join-Path $Stage "pipeline"
New-Item -ItemType Directory -Force -Path $PipelineDir | Out-Null
$FileList = Get-Content (Join-Path $Root "packaging\pipeline_files.txt") |
    Where-Object { $_ -and -not $_.StartsWith("#") }
foreach ($rel in $FileList) {
    $src = Join-Path $Root $rel.Trim()
    if (-not (Test-Path $src)) { throw "Missing pipeline file: $src" }
    Copy-Item -Force $src (Join-Path $PipelineDir (Split-Path $rel -Leaf))
}

# Place build_info next to the exe for easy inspection.
Copy-Item -Force $BuildInfo (Join-Path $Stage "build_info.json")

# README for portable users
@"
Macro Review portable build
===========================

Version: $AppVersion
Channel: $Channel
Commit:  $GitCommit

1. Extract this folder anywhere (no installer).
2. Double-click MacroReview.exe
3. On first run, open Setup and install the managed ML environment.
   You need a 64-bit system Python 3.11+ on PATH (python.org).
4. Settings / cache / the managed venv live under %LOCALAPPDATA%\MacroReview
   and survive replacing this folder with a newer build.

Unsigned builds may trigger Windows SmartScreen — choose More info → Run anyway.
Updates are notification-only: Settings → Check for updates opens the GitHub release page.
"@ | Set-Content -Path (Join-Path $Stage "README.txt") -Encoding utf8

if (-not $SkipZip) {
    if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
    Compress-Archive -Path $Stage -DestinationPath $ZipPath -Force
    $Hash = (Get-FileHash -Algorithm SHA256 -Path $ZipPath).Hash.ToLowerInvariant()
    Set-Content -Path $ChecksumPath -Value "$Hash  $(Split-Path $ZipPath -Leaf)" -Encoding ascii
    $Manifest = [ordered]@{
        name       = $OutName
        version    = $AppVersion
        channel    = $Channel
        commit     = $GitCommit
        built_at   = $BuildInfoObj.built_at
        zip        = (Split-Path $ZipPath -Leaf)
        sha256     = $Hash
        repo       = "mythorath/macro-review"
    }
    $Manifest | ConvertTo-Json | ForEach-Object {
        [System.IO.File]::WriteAllText($ManifestPath, $_)
    }
    Write-Host "Created $ZipPath"
    Write-Host "SHA256 $Hash"
}

Write-Host "Stage folder: $Stage"
Write-Host "Done."
