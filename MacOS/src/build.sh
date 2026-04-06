#!/bin/bash
# VibeVPN macOS Build — produces a single VibeVPN.app
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLIENT_DIR="$(dirname "$SCRIPT_DIR")"
ELECTRON_DIR="$CLIENT_DIR/electron"

echo "=== VibeVPN macOS Build ==="

# ── Find Python ───────────────────────────────────────────────────────
PYTHON=""
for p in "$SCRIPT_DIR/.venv/bin/python3" "$CLIENT_DIR/.venv/bin/python3" \
         "$CLIENT_DIR/../.venv/bin/python3" "$HOME/Documents/.venv/bin/python3" "python3"; do
    if command -v "$p" &>/dev/null && "$p" -c "import websockets" 2>/dev/null; then
        PYTHON="$p"; break
    fi
done
[ -z "$PYTHON" ] && echo "Error: Python with websockets not found" && exit 1
echo "[1/3] Python: $PYTHON"

# ── Build vpn-helper ──────────────────────────────────────────────────
echo "[2/3] Building vpn-helper..."
cd "$SCRIPT_DIR"
"$PYTHON" -m PyInstaller --onefile --name vpn-helper \
    --hidden-import websockets --hidden-import tun_darwin --hidden-import common \
    --add-data "$SCRIPT_DIR/common.py:." \
    --add-data "$SCRIPT_DIR/tun_darwin.py:." \
    --clean --noconfirm \
    vpn-helper.py 2>&1 | tail -3

# ── Build Electron .app (vpn-helper is bundled via extraResource) ─────
echo "[3/3] Building VibeVPN.app..."
cd "$ELECTRON_DIR"
[ ! -d node_modules ] && npm install 2>&1 | tail -3
npx electron-forge package 2>&1 | tail -5

# ── Output ────────────────────────────────────────────────────────────
APP="$ELECTRON_DIR/out/VibeVPN-darwin-arm64/VibeVPN.app"
echo ""
echo "=== Build complete ==="
echo "App: $APP"
echo ""
echo "To install on Desktop:"
echo "  cp -R '$APP' ~/Desktop/VibeVPN.app"
