#!/bin/bash
set -e

CERT_DIR="/etc/vpn"
CERT_FILE="$CERT_DIR/cert.pem"
KEY_FILE="$CERT_DIR/key.pem"
SECRET_FILE="$CERT_DIR/.secret"

# ── Generate secret if not provided ────────────────────────────────────────
if [ -z "$VPN_SECRET" ]; then
    if [ -f "$SECRET_FILE" ]; then
        VPN_SECRET=$(cat "$SECRET_FILE")
    else
        VPN_SECRET=$(openssl rand -hex 32)
        install -m 600 /dev/null "$SECRET_FILE"
        echo "$VPN_SECRET" > "$SECRET_FILE"
    fi
    export VPN_SECRET
else
    # Persist provided secret for restarts
    install -m 600 /dev/null "$SECRET_FILE"
    echo "$VPN_SECRET" > "$SECRET_FILE"
fi

# ── Generate self-signed certificate if none mounted ───────────────────────
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    echo "[INIT] No TLS certificate found, generating self-signed..."

    CERT_DOMAIN="${VPN_DOMAIN:-vpn.local}"

    # Build SAN string — include IP if VPN_HOST_IP is set
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
SECRET_MASKED="${VPN_SECRET:0:8}...${VPN_SECRET: -4}"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                    HTTPS VPN Server                         ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                             ║"
echo "║  Status:    RUNNING                                         ║"
echo "║                                                             ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Connection settings for client:                            ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                             ║"
printf "║  Server:    %-47s ║\n" "$EXTERNAL_IP"
printf "║  Port:      %-47s ║\n" "$VPN_PORT"
printf "║  Secret:    %-47s ║\n" "$SECRET_MASKED"
echo "║                                                             ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  To get full secret:                                        ║"
printf "║  docker exec %s cat /etc/vpn/.secret%-19s ║\n" "$HOSTNAME" ""
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                             ║"
printf "║  Max clients: %-44s ║\n" "253"
printf "║  Tunnel subnet: %-41s ║\n" "10.8.0.0/24"
printf "║  TLS cert: %-47s ║\n" "$CERT_FILE"
echo "║                                                             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Start the VPN server ───────────────────────────────────────────────────
exec python3 /app/server.py
