# Local development server launcher for PowerShell
$ErrorActionPreference = "Stop"

Write-Host "Starting Hardonia Storefront local development server..." -ForegroundColor Cyan

if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8020 --reload
} else {
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8020 --reload
}
