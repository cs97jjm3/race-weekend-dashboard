@echo off
title Race Weekend Dashboard - set up
cd /d "%~dp0"
echo.
echo Setting up the Race Weekend Dashboard. This only needs doing once.
echo.
where py >nul 2>nul
if errorlevel 1 (
  echo Python is not installed yet. Installing it now, say Yes if Windows asks.
  winget install -e --id Python.Python.3.13 --accept-source-agreements --accept-package-agreements
  echo.
  echo Python is installed. Close this window and double-click "1 Set up" again to finish.
  pause
  exit /b
)
py -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
  echo.
  echo Something went wrong installing. Check the internet connection and try again.
  pause
  exit /b
)
if not exist config.py copy config.example.py config.py >nul
echo.
echo All done. Double-click "2 Start dashboard" on race weekends,
echo or "Try the demo" to see it working now.
echo.
pause
