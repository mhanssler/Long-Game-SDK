# install_sdk.ps1 - Bootstraps the Long Game SDK environment on Windows
[CmdletBinding()]
param(
    [switch]$NoElevate
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Restart-AsAdministrator {
    if ($NoElevate -or (Test-IsAdministrator)) {
        return
    }

    Write-Host "Long Game SDK setup may need Administrator rights for vendor USB drivers and device permissions." -ForegroundColor Yellow
    Write-Host "Requesting elevation through Windows UAC..." -ForegroundColor Yellow

    $scriptPath = $PSCommandPath
    $argList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$scriptPath`""
    )

    $process = Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}

Restart-AsAdministrator

Write-Host "--- Installing Long Game SDK dependencies ---" -ForegroundColor Cyan
if (Test-IsAdministrator) {
    Write-Host "Running with Administrator rights." -ForegroundColor Green
} else {
    Write-Host "Running without Administrator rights because -NoElevate was specified." -ForegroundColor Yellow
}

# 1. Install uv if not present
if (!(Get-Command "uv" -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..." -ForegroundColor Yellow
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    # Refresh PATH for the current session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
} else {
    Write-Host "uv is already installed." -ForegroundColor Green
}

# 2. Sync the project
Write-Host "Syncing SDK environment..." -ForegroundColor Yellow
uv sync

Write-Host "--- Installation complete! ---" -ForegroundColor Green
Write-Host "Run 'uv run lg-discover' to inventory equipment." -ForegroundColor White
Write-Host "Run 'uv run lg-onboard' to ensure schemas/drivers exist." -ForegroundColor White
Write-Host "Run 'uv run lg-safe' before and after live hardware tests." -ForegroundColor White
