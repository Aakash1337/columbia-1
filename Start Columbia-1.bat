@echo off
REM ── Columbia-1 launcher ──────────────────────────────────────────────────────
REM Starts the unified web app. Prefers a local .venv; falls back to the venv of
REM the sibling TTS repo (which already has torch/chatterbox/edge-tts installed),
REM so a fresh Columbia-1 checkout can run without a second heavy install.

setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=..\TTS\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" serve.py %*
endlocal
