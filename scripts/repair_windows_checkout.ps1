<#
.SYNOPSIS
Repair a Windows checkout that is running a stale Long Game SDK auto-onboarder.

.DESCRIPTION
This script is intentionally conservative. It fixes the exact failure mode seen
when Windows has local generated state / accidental local edits that prevent the
pushed auto-onboarder fix from being used. It avoids broad repo-wide destructive
cleanup commands.

Run from the Long-Game-SDK repo root in PowerShell:

    powershell -ExecutionPolicy Bypass -File .\scripts\repair_windows_checkout.ps1

Use -RunAutoOnboard to run a one-shot auto-onboard scan after repair.
#>

param(
    [switch]$RunAutoOnboard
)

$ErrorActionPreference = "Stop"

function Write-Step($Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Require-Command($Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH. Install it, then retry."
    }
}

Write-Step "Checking prerequisites"
Require-Command git
Require-Command uv

$repoRoot = (git rev-parse --show-toplevel).Trim()
Set-Location $repoRoot
Write-Host "Repo: $repoRoot"

Write-Step "Current status"
git status --short --branch

Write-Step "Removing generated observer state if present"
$statePaths = @(
    "instrument_state.json",
    "src/long_game_sdk/sdk/observers/instrument_state.json"
)
foreach ($path in $statePaths) {
    if (Test-Path $path) {
        Remove-Item $path -Force
        Write-Host "Removed generated state: $path"
    }
}

Write-Step "Restoring files commonly dirtied by local Windows install experiments"
$restorePaths = @(
    "pyproject.toml",
    "uv.lock",
    "src/long_game_sdk/sdk/observers/auto_onboarder.py"
)
foreach ($path in $restorePaths) {
    git restore -- $path 2>$null
}

Write-Step "Fetching and fast-forwarding main"
git fetch origin
git pull --ff-only origin main

Write-Step "Syncing uv environment"
uv sync

Write-Step "Verifying fixed auto-onboarder source"
$autoOnboarder = "src/long_game_sdk/sdk/observers/auto_onboarder.py"
$source = Get-Content $autoOnboarder -Raw
if ($source -match "datasheet_scraper" -or $source -match "/Users/morgan") {
    throw "Stale auto_onboarder.py still contains macOS-only datasheet scraper references."
}
if ($source -notmatch "enrich_identity") {
    throw "auto_onboarder.py does not contain the in-process manual enrichment fix."
}
Write-Host "auto_onboarder.py contains the in-process enrichment fix."

Write-Step "Final status"
git status --short --branch
Write-Host "HEAD:        $(git rev-parse HEAD)"
Write-Host "origin/main: $(git rev-parse origin/main)"

if ($RunAutoOnboard) {
    Write-Step "Running one-shot auto-onboard scan"
    uv run lg-auto-onboard --once
} else {
    Write-Step "Ready"
    Write-Host "Run this when hardware is connected:"
    Write-Host "  uv run lg-auto-onboard --once"
}
