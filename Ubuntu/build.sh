#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== VibeVPN Ubuntu Build ==="

# 1. Build vpn-helper binary from Python source
echo "[1/3] Building vpn-helper with PyInstaller..."
cd src

if ! command -v pyinstaller &> /dev/null; then
    echo "Installing PyInstaller..."
    pip3 install pyinstaller
fi

if ! python3 -c "import websockets" 2>/dev/null; then
    echo "Installing websockets..."
    pip3 install websockets
fi

pyinstaller --onefile \
    --name vpn-helper \
    --add-data "common.py:." \
    --add-data "tun_linux.py:." \
    --hidden-import websockets \
    --hidden-import websockets.legacy \
    --hidden-import websockets.legacy.client \
    --hidden-import websockets.legacy.server \
    vpn-helper.py

echo "  -> Built: src/dist/vpn-helper"

# 2. Build Electron app
echo "[2/3] Building Electron app..."
cd "$SCRIPT_DIR/electron"

if [ ! -d "node_modules" ]; then
    echo "Installing npm dependencies..."
    npm install
fi

# Copy tray icon if missing
if [ ! -f "assets/tray.png" ]; then
    echo "  Note: Place a 22x22 tray icon at electron/assets/tray.png"
    # Create a placeholder
    mkdir -p assets
    convert -size 22x22 xc:transparent -fill '#22c55e' -draw "circle 11,11 11,1" assets/tray.png 2>/dev/null || \
    echo "  (skipping icon generation — ImageMagick not found)"
fi

npm run make

echo "[3/3] Done!"
echo ""
echo "Output packages:"
ls -la out/make/ 2>/dev/null || echo "  Check electron/out/make/ for .deb packages"
echo ""
echo "To install the helper manually:"
echo "  sudo cp src/dist/vpn-helper /opt/vibevpn/vpn-helper"
echo "  sudo cp vibevpn-helper.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now vibevpn-helper"
