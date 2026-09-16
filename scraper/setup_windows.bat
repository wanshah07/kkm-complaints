@echo off
REM One-time setup on Windows: virtualenv, dependencies, Chromium, .env template.
REM Works whether Python is on PATH as "python" or only via the "py" launcher.
setlocal
cd /d "%~dp0"

set PY=
py --version >nul 2>&1 && set PY=py
if "%PY%"=="" ( python --version >nul 2>&1 && set PY=python )
if "%PY%"=="" (
  echo.
  echo Python was not found.
  echo Install Python 3.12 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" on the first installer screen.
  echo Then close this window, open a NEW one, and run this file again.
  echo.
  pause
  exit /b 1
)
echo Using Python launcher: %PY%
%PY% --version

if not exist .venv (
  echo Creating the virtual environment...
  %PY% -m venv .venv || ( echo Could not create .venv & pause & exit /b 1 )
)
call .venv\Scripts\activate.bat || ( echo Could not activate .venv & pause & exit /b 1 )

echo Installing dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt || ( echo Dependency install failed & pause & exit /b 1 )

echo Installing Chromium for Playwright...
python -m playwright install chromium || ( echo Chromium install failed & pause & exit /b 1 )

if not exist .env copy .env.example .env >nul

echo.
echo ======================================================================
echo  Setup complete.
echo.
echo  1. Edit the .env file in this folder (right-click, Open with Notepad)
echo     and fill in:
echo        APPS_SCRIPT_WEBHOOK_URL
echo        APPS_SCRIPT_API_TOKEN
echo        ANTHROPIC_API_KEY
echo     For Instagram and Facebook logins also set:
echo        PW_STORAGE_STATE_PATH=storage_state.json
echo     and put your storage_state.json file in this same folder.
echo.
echo  2. Test with:   run_windows.bat --dry-run --platform Instagram
echo     Then read:   logs\run-^<today^>.log
echo.
echo  3. Schedule it: double-click schedule_windows.bat
echo ======================================================================
echo.
pause
endlocal
