# ReelGist - Startup Script (Windows PowerShell)
# Run from the repo root: .\start.ps1

$ErrorActionPreference = "Stop"
$repoRoot = $PSScriptRoot

# -- Use the project virtualenv ------------------------------------------------
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "ERROR: .venv not found. Create it with:" -ForegroundColor Red
    Write-Host "  python -m venv .venv" -ForegroundColor Cyan
    Write-Host "  .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt" -ForegroundColor Cyan
    exit 1
}

# -- Refuse to start if a previous instance still holds the ports ---------------
# (otherwise the new server fails to bind and the OLD one keeps serving old code)
$busy = @()
foreach ($port in 5050, 8080) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        $started = if ($proc) { $proc.StartTime.ToString("HH:mm:ss") } else { "?" }
        $busy += "  port $port is in use by PID $($conn.OwningProcess) ($($proc.ProcessName), started $started)"
    }
}
if ($busy.Count -gt 0) {
    Write-Host "ERROR: ReelGist looks like it is already running:" -ForegroundColor Red
    $busy | ForEach-Object { Write-Host $_ -ForegroundColor Yellow }
    Write-Host "Close the old ReelGist windows (or run Stop-Process -Id <PID>) and try again." -ForegroundColor Cyan
    exit 1
}

Write-Host ""
Write-Host "  ReelGist - Instagram Reel Transcriber" -ForegroundColor Magenta
Write-Host ""

# -- Check .env ----------------------------------------------------------------
$envFile    = Join-Path $repoRoot "backend\.env"
$envExample = Join-Path $repoRoot "backend\.env.example"

if (-not (Test-Path $envFile)) {
    Copy-Item $envExample $envFile
    Write-Host "WARNING: Created backend\.env - please add your GROQ_API_KEY!" -ForegroundColor Yellow
    Write-Host "         Get a free key at: https://console.groq.com" -ForegroundColor Cyan
    Write-Host ""
    notepad $envFile
    Read-Host "Press Enter after saving your API key"
}

# Quick check key is set
$envContent = Get-Content $envFile -Raw
if ($envContent -match "gsk_your_key_here") {
    Write-Host "WARNING: GROQ_API_KEY still looks like a placeholder." -ForegroundColor Yellow
    Write-Host "         Edit backend\.env and add your real key from https://console.groq.com" -ForegroundColor Cyan
    $ans = Read-Host "Continue anyway? [y/N]"
    if ($ans -ne "y") { exit 1 }
}

# -- Check ffmpeg (optional) ---------------------------------------------------
$ffmpegOk = $null -ne (Get-Command ffmpeg -ErrorAction SilentlyContinue)
if (-not $ffmpegOk) {
    Write-Host ""
    Write-Host "NOTE: ffmpeg not found - app will still work without it." -ForegroundColor DarkYellow
    Write-Host "      To install: winget install --id Gyan.FFmpeg -e" -ForegroundColor DarkGray
    Write-Host ""
}

# -- Start Flask backend -------------------------------------------------------
Write-Host "Starting Python backend on http://localhost:5050 ..." -ForegroundColor Green
$backendDir = Join-Path $repoRoot "backend"
$backendJob = Start-Process -FilePath $python `
    -ArgumentList "app.py" `
    -WorkingDirectory $backendDir `
    -PassThru `
    -WindowStyle Normal

Start-Sleep -Seconds 2

# -- Start frontend HTTP server ------------------------------------------------
Write-Host "Starting frontend on http://localhost:8080 ..." -ForegroundColor Green
$frontendDir = Join-Path $repoRoot "frontend"
$frontendJob = Start-Process -FilePath $python `
    -ArgumentList "-m", "http.server", "8080", "--bind", "127.0.0.1", "--directory", $frontendDir `
    -WorkingDirectory $repoRoot `
    -PassThru `
    -WindowStyle Minimized

Start-Sleep -Seconds 1

# -- Open browser --------------------------------------------------------------
Write-Host "Opening http://localhost:8080 in your browser..." -ForegroundColor Cyan
try { Start-Process "http://localhost:8080" } catch { Write-Host "  (Could not auto-open browser - navigate there manually)" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "ReelGist is running!" -ForegroundColor Green
Write-Host "  Frontend : http://localhost:8080" -ForegroundColor White
Write-Host "  Backend  : http://localhost:5050" -ForegroundColor White
Write-Host ""
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray

# Keep alive so Ctrl+C kills both processes
try {
    Wait-Process -Id $backendJob.Id
} finally {
    Write-Host ""
    Write-Host "Shutting down..." -ForegroundColor DarkGray
    Stop-Process -Id $frontendJob.Id -ErrorAction SilentlyContinue
    Stop-Process -Id $backendJob.Id  -ErrorAction SilentlyContinue
}
