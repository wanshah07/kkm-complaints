@echo off
REM One-time setup on Windows: virtualenv, dependencies, Chromium, .env template.
REM Needs Python 3.11+ on PATH (python.org installer, tick "Add python.exe to PATH").
setlocal
cd /d "%~dp0"
python --version || (echo Python not found. Install from https://www.python.org/downloads/ and tick "Add to PATH". & pause & exit /b 1)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
if not exist .env copy .env.example .env
echo.
echo Setup done. Now edit .env (notepad .env) and fill in:
echo   APPS_SCRIPT_WEBHOOK_URL, APPS_SCRIPT_API_TOKEN, ANTHROPIC_API_KEY
echo Then test with:  run_windows.bat --dry-run
echo.
pause
endlocal
