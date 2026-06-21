@echo off
cd /d "%~dp0"
title SR Mods Launcher

rem Run the launcher from source (no .exe, no antivirus false positives).
rem Double-click this file to open the launcher. Needs Python 3.8+ installed.

set "PY=%LOCALAPPDATA%\Programs\Python\Python39\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -c "import requests" 2>nul
if errorlevel 1 "%PY%" -m pip install requests

"%PY%" launcher.py
if errorlevel 1 (
  echo.
  echo Failed to start. Make sure Python 3.8+ is installed from python.org
  pause
)
