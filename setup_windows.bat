@echo off
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto error
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if errorlevel 1 goto error
python -m playwright install chromium
if errorlevel 1 goto error
python -m jobpilot init
if errorlevel 1 goto error
echo.
echo Setup complete. Open data\profile.json and follow START_HERE.md.
pause
exit /b 0
:error
echo Setup failed. Read the error above. Install Python 3.10 or later and retry.
pause
exit /b 1
