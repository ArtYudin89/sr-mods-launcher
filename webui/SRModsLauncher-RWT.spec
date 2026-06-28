# -*- mode: python ; coding: utf-8 -*-
# RWT-сборка нового интерфейса (pywebview) с ВШИТЫМ токеном (embedded_secrets.py).
# Идентична SRModsLauncher.spec, отличается только именем выходного файла — чтобы
# RWT-exe (с токеном) не путали с публичным. Запуск из корня репозитория:
#   cd webui && python -m PyInstaller --clean --noconfirm SRModsLauncher-RWT.spec
# Предварительно build_rwt.bat генерирует embedded_secrets.py из launcher_config.json.
from PyInstaller.utils.hooks import collect_all

datas = [('web', 'web')]            # фронт (HTML/CSS/JS) → _MEIPASS/web
binaries = []
hiddenimports = ['launcher_core', 'embedded_secrets', 'clr', 'requests']

for pkg in ('webview',):            # pywebview + WebView2-loader + clr-данные
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ['app.py'],
    pathex=['.', '..'],             # '..' — чтобы найти launcher_core и embedded_secrets в корне репо
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
    name='SRModsLauncher-RWT',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,                  # окно без консоли
    disable_windowed_traceback=False,
)
