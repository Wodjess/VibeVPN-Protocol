# VibeVPN macOS - Build Instructions

## Prerequisites

- **macOS 13+** (Ventura or later)
- **Python 3.12+** (`brew install python@3.12`)
- **Node.js 20+** (`brew install node`)
- **PyInstaller** (`pip3 install pyinstaller websockets`)

## Step 1: Build the VPN helper (Python -> binary)

```bash
cd src/

# Install Python dependencies
pip3 install websockets pyinstaller

# Build the helper binary
pyinstaller --onefile --name vpn-helper vpn-helper.py \
    --hidden-import websockets \
    --hidden-import websockets.legacy \
    --hidden-import websockets.legacy.client \
    --add-data "common.py:." \
    --add-data "tun_darwin.py:."

# Output: dist/vpn-helper
```

## Step 2: Build the Electron app

```bash
cd electron/

# Install Node.js dependencies
npm install

# Build for macOS
npx electron-forge package

# Output: out/VibeVPN-darwin-*/VibeVPN.app
```

## Step 3: Create DMG installer

```bash
# Copy the built .app to VibeDMG/
cp -R electron/out/VibeVPN-darwin-*/VibeVPN.app VibeDMG/VibeVPN.app

# Run the DMG creation script
cd VibeDMG/
chmod +x make_dmg.sh
./make_dmg.sh

# Output: VibeDMG/VibeVPN.dmg
```

## Full rebuild (all steps)

```bash
# 1. Helper
cd src && pip3 install websockets pyinstaller
pyinstaller --onefile --name vpn-helper vpn-helper.py \
    --hidden-import websockets --hidden-import websockets.legacy \
    --hidden-import websockets.legacy.client \
    --add-data "common.py:." --add-data "tun_darwin.py:."
cd ..

# 2. Electron
cd electron && npm install && npx electron-forge package && cd ..

# 3. DMG
cp -R electron/out/VibeVPN-darwin-*/VibeVPN.app VibeDMG/VibeVPN.app
cd VibeDMG && ./make_dmg.sh
```

## Notes

- The helper requires `vpn-helper.py`, `tun_darwin.py`, and `common.py` in the same directory for building
- The Electron app looks for `vpn-helper` in `src/dist/` (set in `forge.config.js` extraResource)
- The DMG script (`make_dmg.sh`) uses `photo.png` as background image
- To modify the DMG layout (icon positions, window size), edit `make_dmg.sh`
