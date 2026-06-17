@echo off
REM Сборка SR Mods Launcher в один .exe (PyInstaller).
REM Требуется: Python 3.8+, pip install -r requirements.txt pyinstaller
setlocal

where pyinstaller >nul 2>nul
if errorlevel 1 (
    echo PyInstaller не найден. Устанавливаю...
    python -m pip install pyinstaller
)

echo Сборка SRModsLauncher.exe ...
pyinstaller --noconfirm --clean ^
  --onefile --windowed ^
  --name SRModsLauncher ^
  --add-data "theme.json;." ^
  launcher.py

echo.
echo Готово: dist\SRModsLauncher.exe
echo (theme.json встроен; можно положить свой theme.json рядом с .exe для переопределения)
endlocal
