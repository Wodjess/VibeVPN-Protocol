@echo off
:: VibeVPN Start — connects VPN and adds to autostart
:: Fully offline — all dependencies must be pre-bundled via prepare.bat

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [VibeVPN] Requesting admin privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

:: ── Verify all files exist ──────────────────────────────────────────────
if not exist "node\node.exe" (
    echo [VibeVPN] ERROR: node\node.exe not found.
    echo [VibeVPN] Run prepare.bat first (requires internet once^).
    pause
    exit /b 1
)
if not exist "node_modules\ws" (
    echo [VibeVPN] ERROR: node_modules not found.
    echo [VibeVPN] Run prepare.bat first (requires internet once^).
    pause
    exit /b 1
)
if not exist "config.json" (
    echo [VibeVPN] ERROR: config.json not found.
    pause
    exit /b 1
)
if not exist "wintun.dll" (
    echo [VibeVPN] ERROR: wintun.dll not found.
    pause
    exit /b 1
)

:: ── Kill any existing instance ──────────────────────────────────────────
taskkill /F /FI "WINDOWTITLE eq VibeVPN" >nul 2>&1
powershell -NoProfile -Command "Get-Process node -ErrorAction SilentlyContinue | Where-Object {$_.CommandLine -like '*vibevpn.js*'} | Stop-Process -Force" >nul 2>&1

:: ── Use bundled Node.js (absolute path) ─────────────────────────────────
set "NODE_EXE=%~dp0node\node.exe"
for /f "tokens=*" %%v in ('"%NODE_EXE%" -v') do echo [VibeVPN] Node.js %%v

:: ── Generate autostart script with absolute paths ───────────────────────
set "VPN_DIR=%~dp0."
> "%~dp0_autostart.bat" (
    echo @echo off
    echo cd /d "%VPN_DIR%"
    echo "%NODE_EXE%" vibevpn.js
)

:: ── Register autostart via Task Scheduler ───────────────────────────────
:: Use current username so the task runs in the user's session (not SYSTEM).
:: /RL HIGHEST ensures the process runs elevated (required for Wintun/routes).
:: Wrap with cmd /c so Task Scheduler correctly executes the .bat file.
schtasks /Delete /TN "VibeVPN" /F >nul 2>&1
schtasks /Create /F /SC ONLOGON /TN "VibeVPN" /TR "cmd /c \"%~dp0_autostart.bat\"" /RL HIGHEST /RU "%USERNAME%" >nul 2>&1
if %errorlevel% equ 0 (
    echo [VibeVPN] Autostart registered for user %USERNAME%
) else (
    echo [VibeVPN] WARNING: Failed to register autostart via schtasks
    echo [VibeVPN] Trying registry fallback...
    reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v "VibeVPN" /t REG_SZ /d "cmd /c \"%~dp0_autostart.bat\"" /f >nul 2>&1
    if %errorlevel% equ 0 (
        echo [VibeVPN] Autostart registered via registry
    ) else (
        echo [VibeVPN] ERROR: Could not register autostart
    )
)

:: ── Start VPN in background ─────────────────────────────────────────────
echo [VibeVPN] Starting VPN...
start "VibeVPN" /min "%~dp0_autostart.bat"

echo.
echo [VibeVPN] VPN is running in background.
echo [VibeVPN] To stop: run stop.bat
timeout /t 3 >nul
