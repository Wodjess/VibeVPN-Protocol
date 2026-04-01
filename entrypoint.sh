#!/bin/bash
set -e

CERT_DIR="/etc/vpn"
CERT_FILE="$CERT_DIR/cert.pem"
KEY_FILE="$CERT_DIR/key.pem"
USERS_FILE="$CERT_DIR/users.json"

# ── Create default admin user if no users exist ───────────────────────────
if [ ! -f "$USERS_FILE" ] || [ "$(cat "$USERS_FILE" 2>/dev/null)" = "{}" ]; then
    DEFAULT_USER="${VPN_ADMIN_USER:-admin}"
    DEFAULT_PASS="${VPN_ADMIN_PASS:-$(openssl rand -hex 8)}"
    python3 /app/users.py add "$DEFAULT_USER" "$DEFAULT_PASS"
    echo "[INIT] Created default user: $DEFAULT_USER / $DEFAULT_PASS"
    # Save password to file so it can be retrieved later
    echo "$DEFAULT_PASS" > "$CERT_DIR/.admin_pass"
    chmod 600 "$CERT_DIR/.admin_pass"
fi

# ── Generate self-signed certificate if none mounted ───────────────────────
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    echo "[INIT] No TLS certificate found, generating self-signed..."

    CERT_DOMAIN="${VPN_DOMAIN:-vpn.local}"

    SAN="DNS:$CERT_DOMAIN"
    if [ -n "$VPN_HOST_IP" ]; then
        SAN="$SAN,IP:$VPN_HOST_IP"
    fi

    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
        -keyout "$KEY_FILE" \
        -out "$CERT_FILE" \
        -days 3650 \
        -subj "/CN=$CERT_DOMAIN" \
        -addext "subjectAltName=$SAN" \
        2>/dev/null

    chmod 600 "$KEY_FILE"
    echo "[INIT] Self-signed certificate generated for: $CERT_DOMAIN"
fi

# ── Detect external IP ─────────────────────────────────────────────────────
if [ -n "$VPN_HOST_IP" ]; then
    EXTERNAL_IP="$VPN_HOST_IP"
else
    EXTERNAL_IP="<SET VPN_HOST_IP env var>"
fi

# ── Print connection info ──────────────────────────────────────────────────
VPN_PORT="${VPN_PORT:-443}"

W=80
line()  { printf "║  %-${W}s║\n" "$1"; }
sep()   { printf "╠"; printf '═%.0s' $(seq 1 $((W+2))); printf "╣\n"; }
empty() { printf "║  %-${W}s║\n" ""; }

printf "╔"; printf '═%.0s' $(seq 1 $((W+2))); printf "╗\n"
printf "║  %-${W}s║\n" "HTTPS VPN Server"
sep
empty
line "Status:       RUNNING"
empty
sep
line "Connection settings:"
sep
empty
line "Server:       $EXTERNAL_IP"
line "Port:         $VPN_PORT"
empty
sep
line "Users:"
sep
empty
python3 /app/users.py list 2>/dev/null | while IFS= read -r u; do
    line "$u"
done
empty
sep
line "User management (run inside container):"
sep
empty
line "docker exec VPN vpn-users add <username> <password>"
line "docker exec VPN vpn-users remove <username>"
line "docker exec VPN vpn-users disable <username>"
line "docker exec VPN vpn-users enable <username>"
line "docker exec VPN vpn-users passwd <username> <password>"
line "docker exec VPN vpn-users list"
empty
sep
line "Max clients:  253"
line "Subnet:       10.8.0.0/24"
line "TLS cert:     $CERT_FILE"
empty
printf "╚"; printf '═%.0s' $(seq 1 $((W+2))); printf "╝\n"
echo ""

# ── Start the VPN server ───────────────────────────────────────────────────
exec python3 /app/server.py
