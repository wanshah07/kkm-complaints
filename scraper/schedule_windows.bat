@echo off
REM Registers the Friday 23:30 task in Windows Task Scheduler (run this once, as your user).
REM The PC must be on (not asleep) at that time. Remove with: schtasks /Delete /TN "KKM complaint scraper" /F
setlocal
cd /d "%~dp0"
schtasks /Create /F /TN "KKM complaint scraper" /SC WEEKLY /D FRI /ST 23:30 /TR "\"%~dp0run_windows.bat\"" /RL LIMITED
if %errorlevel%==0 (echo Scheduled: every Friday 23:30 local time. Check Task Scheduler Library for "KKM complaint scraper".) else (echo Failed. Right-click this file and "Run as administrator" if your account is restricted.)
pause
endlocal
