# DocuMind AI Frontend Startup Script
# Usage: .\scripts\start_frontend.ps1

$FRONTEND_DIR = "$PSScriptRoot\..\frontend"

Write-Host "DocuMind AI Frontend Startup" -ForegroundColor Cyan
Write-Host "=============================" -ForegroundColor Cyan

if (-not (Test-Path "$FRONTEND_DIR\package.json")) {
    Write-Host "ERROR: Frontend not found at $FRONTEND_DIR" -ForegroundColor Red
    exit 1
}

Set-Location $FRONTEND_DIR

if (-not (Test-Path "node_modules")) {
    Write-Host "Installing dependencies..." -ForegroundColor Yellow
    npm install
}

Write-Host "Starting frontend dev server..." -ForegroundColor Green
Write-Host "Open: http://localhost:5173" -ForegroundColor Gray
Write-Host "Press Ctrl+C to stop" -ForegroundColor Gray
Write-Host ""

npm run dev
