# Start the planner locally on Windows.
#   .\scripts\dev.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path "$root\.venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv "$root\.venv"
    & "$root\.venv\Scripts\python.exe" -m pip install --upgrade pip
    & "$root\.venv\Scripts\python.exe" -m pip install -r "$root\requirements-dev.txt"
}

if (-not (Test-Path "$root\.env")) {
    Copy-Item "$root\.env.example" "$root\.env"
    Write-Host "Created .env - add your GOOGLE_API_KEY before planning a trip." -ForegroundColor Yellow
}

Set-Location "$root"
& "$root\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
