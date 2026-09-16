@echo off
REM KKM complaint scraper — Windows runner, also used by Task Scheduler.
REM First time: run setup_windows.bat. After that this file does a full run.
REM Logs go to logs\run-YYYYMMDD.log ; report and screenshots to out\.
setlocal
cd /d "%~dp0"

if not exist .venv (
  echo .venv not found. Run setup_windows.bat first.
  pause
  exit /b 1
)
if not exist logs mkdir logs

for /f "tokens=2 delims==" %%I in ('wmic os get LocalDateTime /value 2^>nul ^| find "="') do set DT=%%I
set LOGFILE=logs\run-%DT:~0,8%.log
if "%DT%"=="" set LOGFILE=logs\run.log

call .venv\Scripts\activate.bat
echo ==== %date% %time% start ==== >> "%LOGFILE%"
python main.py %* >> "%LOGFILE%" 2>&1
set RC=%errorlevel%
echo ==== %date% %time% exit %RC% ==== >> "%LOGFILE%"

echo.
echo Finished with exit code %RC%. Last lines of %LOGFILE%:
echo ----------------------------------------------------------------------
powershell -NoProfile -Command "Get-Content '%LOGFILE%' -Tail 40"
echo ----------------------------------------------------------------------
if "%1"=="" goto :eof
pause
endlocal
