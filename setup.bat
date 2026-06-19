@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

if not exist ".env" (
    copy .env.example .env >nul
    echo Created .env — add your free TMDb key at themoviedb.org/settings/api
)

echo.
echo Movie Story Sorter - Setup complete.
echo.
echo Run CLI:     run.bat Marvel
echo Run Web UI:  run_web.bat
echo.
pause
