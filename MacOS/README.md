# VibeVPN macOS Client

Native macOS VPN client built with Electron (UI) + Python privileged helper (VPN daemon). Uses macOS utun for the tunnel interface and WebSocket (WSS) for the encrypted connection.

## Architecture

```
VibeVPN.app (Electron)
  main.js          - Main process, IPC handlers, tray, helper installation
  preload.js       - Context bridge (window.vpn API)
  components/      - React UI (ConnectionPanel, ServerList, AddServerModal)
      |
      | Unix socket (/tmp/vibevpn.sock)
      v
vpn-helper (PyInstaller binary, runs as root via launchd)
  vpn-helper.py    - VPN daemon: WebSocket + utun + routes/DNS
  tun_darwin.py    - macOS utun interface wrapper
  common.py        - Shared constants (MTU, subnet, IP pool)
```

## How it works

1. On first launch, Electron asks for admin password (native macOS dialog)
2. Installs `vpn-helper` to `/Library/PrivilegedHelperTools/com.vibevpn.helper/`
3. Registers launchd plist so the helper runs at boot as root
4. User adds a VPN server and clicks Connect
5. Electron sends commands to vpn-helper over Unix socket (`/tmp/vibevpn.sock`)
6. vpn-helper connects to VPN server via WebSocket, creates utun, configures routes/DNS
7. Packets flow: utun <-> WebSocket <-> VPN server

## Folder structure

| Path | Purpose |
|------|---------|
| `VibeDMG/` | Ready-to-use build: VibeVPN.app, VibeVPN.dmg, make_dmg.sh |
| `VibeDMG/VibeVPN.app` | Built macOS application |
| `VibeDMG/VibeVPN.dmg` | DMG installer image |
| `VibeDMG/make_dmg.sh` | Script to create DMG from .app |
| `VibeDMG/photo.png` | DMG background image |
| `electron/` | Electron app source code |
| `src/` | Python helper source code |
| `src/vpn-helper.py` | VPN daemon (WebSocket + utun + routes) |
| `src/tun_darwin.py` | macOS utun device wrapper |
| `src/common.py` | Shared network constants |
| `src/com.vibevpn.helper.plist` | launchd plist for the helper |

## Requirements

- macOS 13+ (Ventura or later)
- Administrator privileges (for helper installation)
- Python 3.12+ (for building the helper from source)

## Running the pre-built version

1. Open `VibeDMG/VibeVPN.dmg`
2. Drag VibeVPN.app to Applications
3. Launch VibeVPN from Applications
4. Enter admin password when prompted (first launch only)
5. Add your VPN server and click Connect
