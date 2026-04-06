@echo off
:: VibeVPN Prepare — run ONCE with internet to bundle everything locally.
:: After this, the entire WindowsCLI folder is fully self-contained.
:: Distribute via USB, network share, archive — no internet needed on target.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [VibeVPN] Requesting admin privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

echo ============================================
echo   VibeVPN — Preparing offline bundle
echo ============================================
echo.

:: ── Download portable Node.js ───────────────────────────────────────────
if exist "node\node.exe" (
    echo [VibeVPN] Node.js already bundled, skipping download.
) else (
    echo [VibeVPN] Downloading portable Node.js v22.16.0...
    if not exist "node" mkdir node

    curl.exe -L -o "%~dp0node.zip" "https://nodejs.org/dist/v22.16.0/node-v22.16.0-win-x64.zip"
    if not exist "%~dp0node.zip" (
        echo [VibeVPN] ERROR: Download failed. Check internet connection.
        pause
        exit /b 1
    )

    echo [VibeVPN] Extracting Node.js...
    powershell -NoProfile -Command "Expand-Archive -Path '%~dp0node.zip' -DestinationPath '%~dp0_node_tmp' -Force"
    if %errorlevel% neq 0 (
        echo [VibeVPN] ERROR: Extraction failed
        pause
        exit /b 1
    )

    :: Move contents from nested folder to node\
    robocopy "%~dp0_node_tmp\node-v22.16.0-win-x64" "%~dp0node" /E /MOVE >nul 2>&1

    rd /s /q "%~dp0_node_tmp" >nul 2>&1
    del "%~dp0node.zip" >nul 2>&1

    if not exist "node\node.exe" (
        echo [VibeVPN] ERROR: Node.js extraction failed
        pause
        exit /b 1
    )
    echo [VibeVPN] Node.js bundled successfully.
)

:: ── Install npm dependencies locally ────────────────────────────────────
set "PATH=%~dp0node;%PATH%"

set "NEED_NPM=0"
if not exist "node_modules\ws" set "NEED_NPM=1"
if not exist "node_modules\koffi" set "NEED_NPM=1"

if "%NEED_NPM%"=="1" (
    echo [VibeVPN] Installing npm dependencies...
    call "%~dp0node\npm.cmd" install --production
    if %errorlevel% neq 0 (
        echo [VibeVPN] ERROR: npm install failed
        pause
        exit /b 1
    )
    echo [VibeVPN] Dependencies installed.
) else (
    echo [VibeVPN] Dependencies already installed, skipping.
)

:: ── Verify bundle ───────────────────────────────────────────────────────
echo.
echo [VibeVPN] Verifying bundle...

set "OK=1"
if not exist "node\node.exe"    ( echo   MISSING: node\node.exe    & set "OK=0" )
if not exist "node_modules\ws"  ( echo   MISSING: node_modules\ws  & set "OK=0" )
if not exist "node_modules\koffi" ( echo   MISSING: node_modules\koffi & set "OK=0" )
if not exist "wintun.dll"       ( echo   MISSING: wintun.dll       & set "OK=0" )
if not exist "vibevpn.js"       ( echo   MISSING: vibevpn.js       & set "OK=0" )
if not exist "config.json"      ( echo   MISSING: config.json      & set "OK=0" )

if "%OK%"=="1" (
    echo.
    echo ============================================
    echo   Bundle ready! All files are local.
    echo   Copy the entire WindowsCLI folder to
    echo   any Windows machine and run start.bat
    echo ============================================
) else (
    echo.
    echo [VibeVPN] ERROR: Bundle incomplete, see above.
)

echo.
pause
