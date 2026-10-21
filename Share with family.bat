@echo off
title Race Weekend Dashboard - sharing
cd /d "%~dp0"
where cloudflared >nul 2>nul
if errorlevel 1 (
  echo Installing the free sharing tool. Say Yes if Windows asks.
  winget install -e --id Cloudflare.cloudflared --accept-source-agreements --accept-package-agreements
  echo.
  echo Installed. Close this window and double-click "Share with family" again.
  pause
  exit /b
)
echo Starting the dashboard with a private link. The link and code appear below
echo and at the top of the dashboard. Close this window to stop sharing.
py dashboard.py --share
pause
