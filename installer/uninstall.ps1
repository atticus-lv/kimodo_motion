<#
.SYNOPSIS
    Kimodo Motion — Clean uninstall.

.DESCRIPTION
    Removes ~/.kimodo_venv and ~/.kimodo_runtime.
    Prompts before deleting the 16GB HuggingFace model cache.

.PARAMETER Yes
    Skip all confirmations.

.PARAMETER KeepCache
    Keep the HuggingFace model cache (~16GB) — useful if reinstalling.

.PARAMETER PurgeCache
    Also delete HF model cache without asking.
#>

[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$KeepCache,
    [switch]$PurgeCache
)

$ErrorActionPreference = "Stop"

$VENV_PATH   = Join-Path $env:USERPROFILE ".kimodo_venv"
$RUNTIME_DIR = Join-Path $env:USERPROFILE ".kimodo_runtime"
$HF_HUB      = Join-Path $env:USERPROFILE ".cache\huggingface\hub"

function Confirm-Action {
    param([string]$Question)
    if ($Yes) { return $true }
    $resp = Read-Host "$Question [y/N]"
    return ($resp -match '^(y|yes)$')
}

function Format-Size {
    param([long]$Bytes)
    if ($Bytes -gt 1GB) { return "{0:N2} GB" -f ($Bytes / 1GB) }
    if ($Bytes -gt 1MB) { return "{0:N2} MB" -f ($Bytes / 1MB) }
    return "{0:N0} KB" -f ($Bytes / 1KB)
}

function Get-DirSize {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return 0L }
    $sum = 0L
    Get-ChildItem -Path $Path -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
        if (-not $_.PSIsContainer) { $sum += $_.Length }
    }
    return $sum
}

Write-Host "========== Kimodo Motion Uninstaller ==========" -ForegroundColor Cyan

# 1. venv
if (Test-Path $VENV_PATH) {
    $size = Get-DirSize $VENV_PATH
    Write-Host ""
    Write-Host "venv: $VENV_PATH  ($(Format-Size $size))" -ForegroundColor Yellow
    if (Confirm-Action "Delete venv?") {
        Write-Host "Removing $VENV_PATH ..."
        Remove-Item -Recurse -Force $VENV_PATH
        Write-Host "  removed" -ForegroundColor Green
    } else {
        Write-Host "  kept" -ForegroundColor Gray
    }
} else {
    Write-Host "venv not present — skip" -ForegroundColor Gray
}

# 2. runtime dir (includes log, optional python portable)
if (Test-Path $RUNTIME_DIR) {
    $size = Get-DirSize $RUNTIME_DIR
    Write-Host ""
    Write-Host "runtime: $RUNTIME_DIR  ($(Format-Size $size))" -ForegroundColor Yellow
    if (Confirm-Action "Delete runtime dir (includes install.log, portable python if any)?") {
        Write-Host "Removing $RUNTIME_DIR ..."
        Remove-Item -Recurse -Force $RUNTIME_DIR
        Write-Host "  removed" -ForegroundColor Green
    } else {
        Write-Host "  kept" -ForegroundColor Gray
    }
} else {
    Write-Host "runtime dir not present — skip" -ForegroundColor Gray
}

# 3. HF cache (big decision — default KEEP)
if (Test-Path $HF_HUB) {
    $kimodoDirs = Get-ChildItem $HF_HUB -Directory -Filter "models--nvidia--Kimodo-*" -ErrorAction SilentlyContinue
    $llamaDir   = Get-ChildItem $HF_HUB -Directory -Filter "models--meta-llama--Meta-Llama-3-*" -ErrorAction SilentlyContinue

    $targets = @()
    if ($kimodoDirs) { $targets += $kimodoDirs }
    if ($llamaDir)   { $targets += $llamaDir }

    if ($targets.Count -gt 0) {
        $totalSize = 0L
        foreach ($t in $targets) { $totalSize += Get-DirSize $t.FullName }
        Write-Host ""
        Write-Host "HuggingFace model cache: $(Format-Size $totalSize) across $($targets.Count) repo(s)" -ForegroundColor Yellow
        foreach ($t in $targets) {
            $s = Get-DirSize $t.FullName
            Write-Host "  $($t.Name)  ($(Format-Size $s))"
        }
        Write-Host "Re-downloading these takes hours + requires HF auth for LLaMA!" -ForegroundColor Red

        $doDelete = $false
        if ($PurgeCache) { $doDelete = $true }
        elseif ($KeepCache) { $doDelete = $false }
        else { $doDelete = Confirm-Action "Delete HF model cache? (recommended: keep)" }

        if ($doDelete) {
            foreach ($t in $targets) {
                Write-Host "Removing $($t.FullName) ..."
                Remove-Item -Recurse -Force $t.FullName
            }
            Write-Host "  HF cache purged" -ForegroundColor Green
        } else {
            Write-Host "  HF cache kept" -ForegroundColor Gray
        }
    } else {
        Write-Host "No kimodo/llama models in HF cache — skip" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "========== Uninstall complete ==========" -ForegroundColor Cyan
Write-Host "To reinstall: run installer/install.ps1"
if (-not $Yes) {
    Write-Host "Press Enter to close..."
    [void][System.Console]::ReadLine()
}
