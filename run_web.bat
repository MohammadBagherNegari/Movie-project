@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Run setup.bat first.
    pause
    exit /b 1
)

echo Starting web UI...
echo Link: http://127.0.0.1:5000
.venv\Scripts\python.exe run_web.py
