@echo off
setlocal
set "SCRIPT=%~dp0Invoke-TradingOSDestinationIntake.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
