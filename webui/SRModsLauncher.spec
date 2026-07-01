# -*- mode: python ; coding: utf-8 -*-
# Сборка нового интерфейса (pywebview). Запуск:
#   pyinstaller webui/SRModsLauncher.spec   (из корня репозитория)
from PyInstaller.utils.hooks import collect_all

datas = [('web', 'web'), ('Icon.ico', '.')]   # фронт (HTML/CSS/JS) + иконка → _MEIPASS
binaries = []
hiddenimports = ['launcher_core', 'embedded_secrets', 'clr', 'requests']

for pkg in ('webview',):            # pywebview + WebView2-loader + clr-данные
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ['app.py'],
    pathex=['.', '..'],             # '..' — чтобы найти launcher_core в корне репо
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter'],           # старый GUI в новый exe не тащим
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SRModsLauncher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,                  # окно без консоли
    disable_windowed_traceback=False,
    icon='Icon.ico',               # иконка exe/окна/панели задач
)
