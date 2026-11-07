@echo off
cd /d "%~dp0"
echo Making a one-minute video of the latest race...
py -m pip install -q -r requirements-video.txt
py tools\race_video.py
echo.
echo The video is in the videos folder.
start "" videos
pause
