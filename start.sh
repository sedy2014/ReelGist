#!/usr/bin/env bash
# ReelGist - Startup Script (macOS / Linux)
# Run from the repo root: ./start.sh
set -euo pipefail
cd "$(dirname "$0")"

# -- Use the project virtualenv ------------------------------------------------
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "ERROR: .venv not found. Create it with:"
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/python -m pip install -r backend/requirements.txt"
    exit 1
fi

# -- Refuse to start if a previous instance still holds the ports ---------------
# (otherwise the new server fails to bind and the OLD one keeps serving old code)
for port in 5050 8080; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
        echo "ERROR: port $port is already in use - ReelGist may already be running."
        echo "Stop the old instance (e.g. 'lsof -i :$port' to find it) and try again."
        exit 1
    fi
done

echo ""
echo "  ReelGist - Instagram Reel Transcriber"
echo ""

# -- Check .env ----------------------------------------------------------------
if [ ! -f backend/.env ]; then
    cp backend/.env.example backend/.env
    echo "Created backend/.env - add your GROQ_API_KEY to it, then run ./start.sh again."
    echo "Get a free key at: https://console.groq.com"
    exit 1
fi
if grep -q "gsk_your_key_here" backend/.env; then
    echo "WARNING: GROQ_API_KEY in backend/.env still looks like a placeholder."
    read -r -p "Continue anyway? [y/N] " ans
    [ "$ans" = "y" ] || exit 1
fi

# -- Check ffmpeg (optional) ---------------------------------------------------
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "NOTE: ffmpeg not found - the app still works, but very long reels will fail."
    echo "      To install: brew install ffmpeg (macOS) or sudo apt install ffmpeg (Debian/Ubuntu)"
    echo ""
fi

# -- Start backend and frontend ------------------------------------------------
echo "Starting Python backend on http://localhost:5050 ..."
(cd backend && exec "../$PY" app.py) &
BACKEND_PID=$!

sleep 2

echo "Starting frontend on http://localhost:8080 ..."
"$PY" -m http.server 8080 --bind 127.0.0.1 --directory frontend >/dev/null 2>&1 &
FRONTEND_PID=$!

# Ctrl+C (or the backend exiting) stops both processes
trap 'echo ""; echo "Shutting down..."; kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true' EXIT INT TERM

sleep 1

# -- Open browser --------------------------------------------------------------
if command -v open >/dev/null 2>&1; then
    open "http://localhost:8080"
elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://localhost:8080" >/dev/null 2>&1 || true
else
    echo "Open http://localhost:8080 in your browser."
fi

echo ""
echo "ReelGist is running!"
echo "  Frontend : http://localhost:8080"
echo "  Backend  : http://localhost:5050"
echo ""
echo "Press Ctrl+C to stop."

wait "$BACKEND_PID"
