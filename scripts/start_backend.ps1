# DocuMind AI Backend Startup Script
# Kills any existing process on port 8000 and starts fresh
# Usage: .\scripts\start_backend.ps1

$PORT = 8000
$BACKEND_DIR = "$PSScriptRoot\..\backend"

Write-Host "DocuMind AI Backend Startup" -ForegroundColor Cyan
Write-Host "============================" -ForegroundColor Cyan

# Kill any existing process on port 8000
$existingProc = netstat -ano | findstr ":$PORT" | findstr "LISTENING" | ForEach-Object {
    ($_ -split "\s+")[-1]
} | Select-Object -First 1

if ($existingProc -and $existingProc -match '^\d+$') {
    Write-Host "Killing existing process on port $PORT (PID: $existingProc)..." -ForegroundColor Yellow
    Stop-Process -Id $existingProc -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# Verify backend directory
if (-not (Test-Path "$BACKEND_DIR\app\main.py")) {
    Write-Host "ERROR: Backend not found at $BACKEND_DIR" -ForegroundColor Red
    exit 1
}

Set-Location $BACKEND_DIR

# Check venv
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "ERROR: Virtual environment not found. Run: python -m venv .venv && .\.venv\Scripts\pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

Write-Host "Starting backend on port $PORT..." -ForegroundColor Green
Write-Host "Logs: $BACKEND_DIR\uvicorn_stdout.log" -ForegroundColor Gray
Write-Host "Press Ctrl+C to stop" -ForegroundColor Gray
Write-Host ""

# Start in foreground (for interactive use)
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
