@echo off
chcp 65001 >nul
title SR Mods Launcher
cd /d "%~dp0"

REM === Запуск лаунчера из исходников (без .exe, без антивирусных ложных тревог) ===
REM Двойной клик по этому файлу открывает лаунчер. Нужен установленный Python 3.8+.

set "PY=%LOCALAPPDATA%\Programs\Python\Python39\python.exe"
if not exist "%PY%" set "PY=python"

REM requests нужен лаунчеру; tkinter входит в стандартный Python
"%PY%" -c "import requests" 2>nul || (
  echo Устанавливаю зависимость requests...
  "%PY%" -m pip install requests
)

"%PY%" launcher.py
if errorlevel 1 (
  echo.
  echo [X] Не удалось запустить. Проверь, что установлен Python 3.8+ (python.org).
  pause
)
