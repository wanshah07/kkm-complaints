@echo off
REM KKM complaint scraper — Windows runner for Task Scheduler (Option B).
REM First time: double-click setup_windows.bat. After that this file does a full run.
REM Logs go to scraper\logs\run-YYYYMMDD.log ; report + screenshots to scraper\out\.
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs
set LOGFILE=logs\run-%date:~-4,4%%date:~-7,2%%date:~-10,2%.log
call .venv\Scripts\activate.bat
echo ==== %date% %time% start ==== >> "%LOGFILE%"
python main.py %* >> "%LOGFILE%" 2>&1
echo ==== %date% %time% exit %errorlevel% ==== >> "%LOGFILE%"
endlocal
