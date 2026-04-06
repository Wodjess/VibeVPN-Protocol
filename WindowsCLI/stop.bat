@echo off
:: VibeVPN Stop — disconnects VPN and removes from autostart
:: Must be run as Administrator

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [VibeVPN] Requesting admin privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: Kill VPN process
echo [VibeVPN] Stopping VPN...
taskkill /F /FI "WINDOWTITLE eq VibeVPN" >nul 2>&1
powershell -NoProfile -Command "Get-Process node -ErrorAction SilentlyContinue | Where-Object {$_.CommandLine -like '*vibevpn.js*'} | Stop-Process -Force" >nul 2>&1

:: Remove from autostart (both Task Scheduler and registry fallback)
schtasks /Delete /TN "VibeVPN" /F >nul 2>&1
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v "VibeVPN" /f >nul 2>&1
del "%~dp0_autostart.bat" >nul 2>&1
echo [VibeVPN] Autostart removed

:: Clean up routes, DNS and IPv6 (in case process was killed mid-connection)
route delete 0.0.0.0 mask 128.0.0.0 >nul 2>&1
route delete 128.0.0.0 mask 128.0.0.0 >nul 2>&1
powershell -NoProfile -Command "Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | Set-DnsClientServerAddress -ResetServerAddresses" >nul 2>&1
powershell -NoProfile -Command "Get-NetAdapterBinding -ComponentId ms_tcpip6 | Where-Object { $_.Enabled -eq $false } | Enable-NetAdapterBinding -ComponentId ms_tcpip6 -Confirm:$false" >nul 2>&1

echo [VibeVPN] VPN stopped.
timeout /t 2 >nul
