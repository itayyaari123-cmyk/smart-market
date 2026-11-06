@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Smart Basket - home sync setup
echo Installing helper libraries...
py -3.12 -m pip install -q --upgrade keyring requests
py -3.12 home_sync.py --setup || goto :fail
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_task.ps1" || goto :fail
echo.
choice /M "Run the first sync now (about 45-60 minutes)"
if errorlevel 2 goto :end
py -3.12 home_sync.py
:end
echo.
echo Done. A log of every run is saved in sync.log
pause
exit /b 0
:fail
echo.
echo Setup failed - send a screenshot of this window.
pause
exit /b 1
