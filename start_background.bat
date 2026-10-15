@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup_windows.bat first.
  pause
  exit /b 1
)
if not exist "data\profile.json" (
  echo Run setup_windows.bat first and complete data\profile.json.
  pause
  exit /b 1
)
if exist "data\worker.stop" del "data\worker.stop"
:run
".venv\Scripts\python.exe" -m jobpilot watch --interval 1200 --prepare
if exist "data\worker.stop" exit /b 0
echo Worker exited. Restarting in 30 seconds. Close this window to stop the launcher.
timeout /t 30 /nobreak >nul
goto run
