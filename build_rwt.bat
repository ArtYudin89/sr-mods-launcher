@echo off
REM ============================================================================
REM  Build SRModsLauncher-RWT.exe  (RWT = Release With Tests) — НОВЫЙ UI (webui).
REM  Test build with the GitHub token BAKED IN so a non-technical tester does
REM  not have to paste anything. The token is read from launcher_config.json
REM  and written to embedded_secrets.py (gitignored, NEVER commit it).
REM  Result -> RWT-раздача\SRModsLauncher-RWT.exe. Hand it to the tester
REM  directly (do NOT publish it; the public build has NO token).
REM ============================================================================
setlocal

where pyinstaller >nul 2>nul
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    python -m pip install pyinstaller
)

echo Generating embedded_secrets.py from launcher_config.json ...
python -c "import json;c=json.load(open('launcher_config.json',encoding='utf-8'));t=c.get('github_token','').strip();r=c.get('repo','ArtYudin89/sr-mods-aggregator').strip();open('embedded_secrets.py','w',encoding='utf-8').write('# AUTO-GENERATED for RWT build. DO NOT COMMIT.\nGITHUB_TOKEN = %r\nREPO = %r\n'%(t,r)); assert t,'no github_token in launcher_config.json'; print('token len',len(t),'repo',r)"
if errorlevel 1 (
    echo FAILED to generate embedded_secrets.py
    exit /b 1
)

echo Building SRModsLauncher-RWT.exe (webui / pywebview) ...
cd webui
python -m PyInstaller --clean --noconfirm SRModsLauncher-RWT.spec
if errorlevel 1 (
    echo FAILED to build
    cd ..
    exit /b 1
)
cd ..

echo Copying to RWT-раздача\ ...
if not exist "RWT-раздача" mkdir "RWT-раздача"
copy /Y "webui\dist\SRModsLauncher-RWT.exe" "RWT-раздача\SRModsLauncher-RWT.exe" >nul

echo.
echo Done: RWT-раздача\SRModsLauncher-RWT.exe   (token embedded, RWT)
echo Hand this file to the tester directly. Do NOT upload to a public release.
endlocal
