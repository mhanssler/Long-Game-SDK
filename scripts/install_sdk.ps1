# install_sdk.ps1 - Bootstraps the Long Game SDK environment on Windows
Write-Host "--- Installing Long Game SDK dependencies ---" -ForegroundColor Cyan

# 1. Install uv if not present
if (!(Get-Command "uv" -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..." -ForegroundColor Yellow
    powershell -Command "irm https://astral.sh/uv/install.ps1 | iex"
    # Refresh PATH for the current session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
} else {
    Write-Host "uv is already installed." -ForegroundColor Green
}

# 2. Sync the project
Write-Host "Syncing SDK environment..." -ForegroundColor Yellow
uv sync

Write-Host "--- Installation complete! ---" -ForegroundColor Green
Write-Host "Run 'uv run lg-check' to verify your VISA/Instrument environment." -ForegroundColor White
