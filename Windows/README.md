# VibeVPN Windows Client

Electron-based VPN client for Windows 10/11. Uses WinTUN for the tunnel interface and WebSocket (WSS) for the encrypted connection to the VPN server.

## Architecture

```
VibeVPN.exe (Electron)
  main.js          - Main process, IPC handlers, tray menu
  vpn-win.js       - VPN logic: WebSocket + WinTUN via koffi (FFI)
  preload.js       - Context bridge (window.vpn API)
  components/      - React UI (ConnectionPanel, ServerList, AddServerModal)

wintun.dll         - WinTUN driver (bundled in resources/)
VibeVPN (Admin).vbs - Launcher that requests UAC elevation
```

## How it works

1. User launches `VibeVPN (Admin).vbs` which requests admin rights and starts `VibeVPN.exe`
2. User adds a VPN server (host, port, username, password) and clicks Connect
3. Electron main process (`vpn-win.js`) connects to the server via WebSocket (WSS)
4. Authenticates with `username:password`, receives assigned IP (e.g. `10.8.0.3`)
5. Creates a WinTUN adapter via `wintun.dll` using koffi (Node.js FFI)
6. Configures routes (all traffic through tunnel) and DNS (1.1.1.1)
7. Forwards IP packets: TUN adapter <-> WebSocket <-> VPN server

## Key files

| File | Purpose |
|------|---------|
| `src/main.js` | Electron main process, window, tray, IPC |
| `src/vpn-win.js` | VPN service: WinTUN + WebSocket + routes/DNS |
| `src/components/App.jsx` | React root: state, connect/disconnect logic |
| `src/components/ConnectionPanel.jsx` | Status display, Connect button, logs |
| `src/components/ServerList.jsx` | Server list, peer display |
| `forge.config.js` | Electron Forge build config |
| `assets/wintun.dll` | WinTUN driver binary (amd64) |
| `assets/VibeVPN.vbs` | VBScript UAC launcher |
| `build/` | Ready-to-use Windows build |

## Requirements

- Windows 10 or later (x64)
- Administrator privileges (for TUN adapter creation)
- Internet connection to VPN server

## Running the pre-built version

1. Open the `build/` folder
2. Double-click `VibeVPN (Admin).vbs`
3. Accept the UAC prompt
4. Add your VPN server and click Connect
