@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_tradingos_decision_brief_sample.ps1"
if errorlevel 1 (
  echo.
  echo Decision Brief sample failed. Review the error above.
  pause
  exit /b 1
)
endlocal
